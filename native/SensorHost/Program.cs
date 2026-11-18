using System.Security.Principal;
using System.Text.Json;
using LibreHardwareMonitor.Hardware;
using Microsoft.Win32;

namespace SensorHost;

internal sealed class UpdateVisitor : IVisitor
{
    public void VisitComputer(IComputer computer) => computer.Traverse(this);

    private readonly HashSet<string> _reported = new();

    public void VisitHardware(IHardware hardware)
    {
        // one failing component must not abort the whole snapshot
        try { hardware.Update(); }
        catch (Exception ex) { ReportOnce(hardware, ex); }
        foreach (IHardware sub in hardware.SubHardware)
        {
            try { sub.Accept(this); }
            catch (Exception ex) { ReportOnce(sub, ex); }
        }
    }

    private void ReportOnce(IHardware hardware, Exception ex)
    {
        string id;
        try { id = hardware.Identifier.ToString(); } catch { id = "?"; }
        if (_reported.Add(id))
            Program.EmitError($"hardware update failed ({id}): {ex.GetType().Name}", fatal: false);
    }

    public void VisitSensor(ISensor sensor) { }
    public void VisitParameter(IParameter parameter) { }
}

internal static class Program
{
    private static readonly object OutLock = new();
    private static Stream? _stdout;
    private static int _interval = 1000;
    private static volatile bool _quit;

    private static void Emit(Dictionary<string, object?> msg)
    {
        if (_quit) return;
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(msg);
        try
        {
            lock (OutLock)
            {
                _stdout ??= Console.OpenStandardOutput();
                _stdout.Write(bytes, 0, bytes.Length);
                _stdout.WriteByte(10);
                _stdout.Flush();
            }
        }
        catch (IOException)
        {
            // the parent closed the pipe: stop cleanly
            _quit = true;
        }
    }

    // fatal=false: one component failed but snapshots keep coming (the UI must not show "stale").
    internal static void EmitError(string message, bool fatal = true) =>
        Emit(new() { ["type"] = "error", ["message"] = message, ["fatal"] = fatal });

    private static bool IsAdmin()
    {
        try
        {
            using var id = WindowsIdentity.GetCurrent();
            return new WindowsPrincipal(id).IsInRole(WindowsBuiltInRole.Administrator);
        }
        catch { return false; }
    }

    private static string? PawnIoVersion()
    {
        try
        {
            foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
            {
                using var baseKey = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view);
                using var key = baseKey.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO");
                if (key?.GetValue("DisplayVersion") is string v && v.Length > 0) return v;
            }
        }
        catch { }
        return null;
    }

    private static double? Num(float? v) =>
        v.HasValue && float.IsFinite(v.Value) ? Math.Round((double)v.Value, 4) : null;

    private static void Collect(IHardware hw, string? parent, List<object> hws, List<object> sensors)
    {
        string id = hw.Identifier.ToString();
        hws.Add(new Dictionary<string, object?>
        {
            ["id"] = id, ["name"] = hw.Name, ["type"] = hw.HardwareType.ToString(), ["parent"] = parent,
        });
        foreach (ISensor s in hw.Sensors)
        {
            sensors.Add(new Dictionary<string, object?>
            {
                ["id"] = s.Identifier.ToString(), ["hw"] = id, ["name"] = s.Name,
                ["type"] = s.SensorType.ToString(), ["index"] = s.Index,
                ["value"] = Num(s.Value), ["min"] = Num(s.Min), ["max"] = Num(s.Max),
            });
        }
        foreach (IHardware sub in hw.SubHardware)
            Collect(sub, id, hws, sensors);
    }

    private static void EmitSnapshot(Computer computer)
    {
        computer.Accept(new UpdateVisitor());
        var hws = new List<object>();
        var sensors = new List<object>();
        foreach (IHardware hw in computer.Hardware)
            Collect(hw, null, hws, sensors);
        Emit(new()
        {
            ["type"] = "snapshot",
            ["t"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            ["hardware"] = hws,
            ["sensors"] = sensors,
        });
    }

    private static int ClampInterval(int ms) => Math.Clamp(ms, 250, 10000);

    public static int Main(string[] args)
    {
        bool once = false;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--once") once = true;
            else if (args[i] == "--interval" && i + 1 < args.Length && int.TryParse(args[i + 1], out int v))
            {
                _interval = ClampInterval(v);
                i++;
            }
        }

        string? pawn = PawnIoVersion();
        Emit(new()
        {
            ["type"] = "hello", ["version"] = "1", ["admin"] = IsAdmin(),
            ["pawnio"] = pawn != null, ["pawnio_version"] = pawn,
            ["os"] = Environment.OSVersion.VersionString,
        });

        Computer? computer = null;
        try
        {
            computer = new Computer
            {
                IsCpuEnabled = true, IsGpuEnabled = true, IsMemoryEnabled = true,
                IsMotherboardEnabled = true, IsStorageEnabled = true, IsControllerEnabled = true,
                IsNetworkEnabled = false,
            };
            computer.Open();

            if (once)
            {
                EmitSnapshot(computer);
                return 0;
            }

            // Commands arrive on stdin; EOF means the parent died.
            var wake = new AutoResetEvent(false);
            var reader = new Thread(() =>
            {
                try
                {
                    string? line;
                    while ((line = Console.In.ReadLine()) != null)
                    {
                        line = line.Trim();
                        if (line == "quit") break;
                        if (line.StartsWith("interval ", StringComparison.Ordinal) &&
                            int.TryParse(line.AsSpan(9), out int ms))
                            Volatile.Write(ref _interval, ClampInterval(ms));
                        // "refresh" and "interval" both just wake the loop for a fresh snapshot.
                        wake.Set();
                    }
                }
                catch { }
                _quit = true;
                wake.Set();
            }) { IsBackground = true };
            reader.Start();

            while (!_quit)
            {
                try { EmitSnapshot(computer); }
                catch (Exception ex) { EmitError("update failed: " + ex.GetType().Name); }
                if (_quit) break;
                wake.WaitOne(Volatile.Read(ref _interval));
            }
            return 0;
        }
        catch (Exception ex)
        {
            EmitError("fatal: " + ex.GetType().Name);
            return 1;
        }
        finally
        {
            try { computer?.Close(); } catch { }
        }
    }
}
