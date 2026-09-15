// Standalone implementation using documented Windows APIs, not copied BetterGI code.
// Reference: BetterGI ChildSessionService and Microsoft Child Sessions documentation.
using System;
using System.IO;
using System.Text;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.ServiceProcess;
using System.Windows.Forms;
using System.Drawing;
using System.Threading.Tasks;

class CycleState {
    public bool Active, Uncertain, Transitioning, QueryFailed;
    public uint? Owned;
    public bool CanPoll { get {return Active && !Transitioning && !Uncertain;} }
    public void ResetForStart() {
        if(Active) throw new InvalidOperationException("上轮尚未结束");
        Uncertain=false; QueryFailed=false; Owned=null;
    }
    public void Complete() { Active=false; Uncertain=false; QueryFailed=false; Owned=null; }
    public bool EnterTransition() {if(Transitioning)return false;Transitioning=true;return true;}
    public void LeaveTransition() {Transitioning=false;}
}

static class Native {
    [DllImport("wtsapi32.dll", SetLastError=true)] public static extern bool WTSIsChildSessionsEnabled(out bool enabled);
    [DllImport("wtsapi32.dll", SetLastError=true)] public static extern bool WTSEnableChildSessions(bool enabled);
    [DllImport("wtsapi32.dll", SetLastError=true)] public static extern bool WTSGetChildSessionId(out uint id);
    [DllImport("wtsapi32.dll", SetLastError=true)] public static extern bool WTSLogoffSession(IntPtr server, uint id, bool wait);
    public static uint? Child() {
        uint id;
        if (!WTSGetChildSessionId(out id)) {
            int error = Marshal.GetLastWin32Error();
            if (error != 1168) throw new InvalidOperationException("子会话查询失败，错误码 " + error);
            return null;
        }
        return id == uint.MaxValue ? (uint?)null : id;
    }
    public static bool Enabled() {
        bool value;
        if (!WTSIsChildSessionsEnabled(out value)) throw new InvalidOperationException("无法查询子会话开关");
        return value;
    }
    public static void Enable(bool value) {
        if (!WTSEnableChildSessions(value)) throw new InvalidOperationException("子会话开关修改失败，错误码 " + Marshal.GetLastWin32Error());
    }
}

[ComImport, Guid("302D8188-0052-4807-806A-362B628F9AC5"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ExtendedSettings {
    void set_Property([In, MarshalAs(UnmanagedType.BStr)] string name, [In, MarshalAs(UnmanagedType.Struct)] ref object value);
    [return: MarshalAs(UnmanagedType.Struct)] object get_Property([In, MarshalAs(UnmanagedType.BStr)] string name);
}

class RdpHost : AxHost {
    public static string Stage = "初始化控件";
    public RdpHost() : base("A0C63C30-F08D-4AB4-907C-34905D770C7D") {}
    object Client { get { return GetOcx(); } }
    static object Get(object o, string name) { Stage="读取 " + name; return o.GetType().InvokeMember(name, BindingFlags.GetProperty, null, o, null); }
    static void Set(object o, string name, object value) { Stage="设置 " + name; o.GetType().InvokeMember(name, BindingFlags.SetProperty, null, o, new object[]{value}); }
    void Call(string name) { Stage="调用 " + name; Client.GetType().InvokeMember(name, BindingFlags.InvokeMethod, null, Client, null); }
    public int Connected { get { return Convert.ToInt32(Get(Client,"Connected")); } }
    public void ConnectLocal() {
        PrepareLocal();
        ConnectPrepared();
    }
    public void ConnectPrepared() {
        ValidatePrepared();
        Call("Connect");
    }
    public void PrepareLocal() {
        Set(Client,"Server","localhost");
        Set(Client,"DesktopWidth",1024); Set(Client,"DesktopHeight",768);
        Set(Client,"ColorDepth",24);
        object advanced = Get(Client,"AdvancedSettings7");
        // Child-session authentication must be configured explicitly, as in BetterGI.
        // This affects this in-process client only, not Windows policy or credentials.
        Set(advanced,"EnableCredSspSupport",true);
        Set(advanced,"RedirectClipboard",false); Set(advanced,"RedirectDrives",false);
        Set(advanced,"RedirectPrinters",false); Set(advanced,"RedirectPorts",false);
        Set(advanced,"RedirectSmartCards",false); Set(advanced,"EnableAutoReconnect",false);
        Set(advanced,"RedirectDevices",false); Set(advanced,"RedirectPOSDevices",false);
        object secured = Get(Client,"SecuredSettings2");
        Set(secured,"KeyboardHookMode",0); Set(secured,"AudioRedirectionMode",2);
        object yes = true;
        Stage="设置 ConnectToChildSession";
        ((ExtendedSettings)Client).set_Property("ConnectToChildSession",ref yes);
        ValidatePrepared();
    }
    public void ValidatePrepared() {
        if(!String.Equals(Convert.ToString(Get(Client,"Server")),"localhost",StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("本机连接目标未生效");
        object advanced=Get(Client,"AdvancedSettings7");
        if(!Convert.ToBoolean(Get(advanced,"EnableCredSspSupport")))
            throw new InvalidOperationException("CredSSP未生效");
        foreach(string name in new string[]{"RedirectClipboard","RedirectDrives","RedirectPrinters","RedirectPorts","RedirectSmartCards","RedirectDevices","RedirectPOSDevices","EnableAutoReconnect"}) {
            if(Convert.ToBoolean(Get(advanced,name))) throw new InvalidOperationException("隔离配置未生效");
        }
        Stage="核对子会话模式";
        if(!Convert.ToBoolean(((ExtendedSettings)Client).get_Property("ConnectToChildSession")))
            throw new InvalidOperationException("子会话模式未生效");
    }
    public void DisconnectLocal() { if (Connected != 0) Call("Disconnect"); }
}

class ProbeForm : Form {
    protected override bool ShowWithoutActivation { get {return true;} }
    public ProbeForm() {ShowInTaskbar=false;Opacity=0;}
}

// A user-launched child-session message-loop probe, not an input/WeChat test.
class HeartbeatProbe : Form {
    public static string ReportPath { get {return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"TianyiBotSessionTrial","heartbeat.txt");} }
    readonly Timer pulse=new Timer();
    readonly Label label=new Label {Dock=DockStyle.Fill};
    readonly string run=Guid.NewGuid().ToString("N");
    readonly uint session;
    long ticks;
    FileStream lease;
    public HeartbeatProbe(uint id) {
        session=id;
        if(Native.Child()!=session || (uint)System.Diagnostics.Process.GetCurrentProcess().SessionId!=session)
            throw new InvalidOperationException("探针仅能在当前子会话运行");
        Directory.CreateDirectory(Path.GetDirectoryName(ReportPath));
        lease=new FileStream(ReportPath+".lock",FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);
        Text="天意Bot 0.5.4 · 分身心跳（不操作微信）";Size=new Size(550,190);Controls.Add(label);
        pulse.Interval=1000;
        pulse.Tick+=delegate {
            try {
                if(Native.Child()!=session){pulse.Stop();label.Text="子会话身份改变，已停止";return;}
                ticks++;
                string data=session+"\n"+run+"\n"+ticks+"\n"+DateTime.UtcNow.Ticks;
                string temp=ReportPath+".new";File.WriteAllText(temp,data);
                if(File.Exists(ReportPath))File.Replace(temp,ReportPath,null);else File.Move(temp,ReportPath);
                label.Text="心跳："+ticks+"\n回主机隐藏分身画面，观察主机面板的计数。\n只验证消息循环；不测试渲染、鼠标、键盘或微信。\n关闭本探针即停止心跳。";
            }catch(Exception){pulse.Stop();label.Text="心跳写入失败，已停止；不自动重试。";}
        };
        FormClosed+=delegate {pulse.Dispose();lease.Dispose();};pulse.Start();
    }
    public static string Read(uint expected, bool hidden, ref string baselineRun, ref long baselineTicks, string path=null) {
        try {
            path=path??ReportPath;
            if(new FileInfo(path).Length>512)return "心跳报告无效";
            string[] p=File.ReadAllLines(path);
            uint id;long count,time;Guid runId;
            if(p.Length!=4 || !uint.TryParse(p[0],out id) || id!=expected || !Guid.TryParseExact(p[1],"N",out runId) || !long.TryParse(p[2],out count) || count<1 || !long.TryParse(p[3],out time))return "尚无本分身的有效心跳";
            double age=(DateTime.UtcNow-new DateTime(time,DateTimeKind.Utc)).TotalSeconds;
            if(age<0 || age>5)return "心跳已过期或停止（不是在线）";
            if(!hidden){baselineRun=null;return "心跳在线："+count+"；请隐藏画面观察";}
            if(baselineRun!=p[1] || count<baselineTicks){baselineRun=p[1];baselineTicks=count;}
            return "隐藏观察：新增 "+(count-baselineTicks)+" 次心跳（仅消息循环，不代表后台输入可用）";
        }catch(Exception){return "未读取到心跳：请在分身内打开同一新版程序";}
    }
}

class TrialForm : Form {
    public static string ErrorDetail(Exception ex) {
        while(ex is TargetInvocationException && ex.InnerException!=null) ex=ex.InnerException;
        return RdpHost.Stage+"："+ex.GetType().Name+"，HRESULT=0x"+ex.HResult.ToString("X8");
    }
    readonly Label status = new Label();
    readonly Button start = new Button();
    readonly Timer timer = new Timer();
    readonly string journal;
    FileStream lease;
    Form viewer;
    RdpHost rdp;
    readonly CycleState cycle = new CycleState();
    bool active {get{return cycle.Active;}set{cycle.Active=value;}}
    bool uncertain {get{return cycle.Uncertain;}set{cycle.Uncertain=value;}}
    uint? owned {get{return cycle.Owned;}set{cycle.Owned=value;}}
    bool originalEnabled, originalServiceStopped, closing, checking;
    DateTime started;
    string heartbeatRun;
    long heartbeatTicks;

    public TrialForm() {
        Text = "天意Bot 0.6.11 · 分身控制器"; Size = new Size(770,460);
        string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"TianyiBotSessionTrial");
        Directory.CreateDirectory(folder); journal = Path.Combine(folder,"recovery.txt");
        lease = new FileStream(Path.Combine(folder,"trial.lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);
        FlowLayoutPanel panel = new FlowLayoutPanel { Dock=DockStyle.Fill, Padding=new Padding(16), FlowDirection=FlowDirection.TopDown, WrapContents=false };
        Controls.Add(panel);
        panel.Controls.Add(new Label { Width=710, Height=100, Text="仅测试本机 Windows 子会话，不启动微信、游戏或机器人。\n子会话使用当前 Windows 用户，系统自启动程序仍可能运行；不是安全沙箱。\n隐藏只隐藏分身画面，控制器必须保留；退出会请求注销本次分身并恢复开关。\n不开放远程连接、不改防火墙；剪贴板、磁盘、打印机重定向关闭。" });
        start.Text="创建空白分身（需要管理员权限）"; start.Width=330;
        start.Click += delegate { Begin(); }; panel.Controls.Add(start);
        Button hide = new Button {Text="隐藏分身画面（保持连接）",Width=330};
        hide.Click += delegate { if(!cycle.Transitioning && viewer!=null) viewer.Hide(); Check(); }; panel.Controls.Add(hide);
        Button show = new Button {Text="显示分身画面",Width=330};
        show.Click += delegate {if(!cycle.Transitioning && viewer!=null) viewer.Show();}; panel.Controls.Add(show);
        Button restore = new Button {Text="结束本次测试并恢复开关",Width=330};
        restore.Click += delegate { EndTrial(); }; panel.Controls.Add(restore);
        status.Width=710; status.Height=90; panel.Controls.Add(status);
        panel.Controls.Add(new Label {Width=710,Height=45,Text="连接后，在分身内打开项目src目录中的“分身内打开微信管理.vbs”。\n请手动登录小号并核对绑定；本控制器不自动启动微信或发送。"});
        status.Text=File.Exists(journal) ? "发现上次恢复记录，禁止新建。请先核查恢复记录："+journal : "尚未创建；打开此面板不会修改系统。";
        start.Enabled=!File.Exists(journal);
        timer.Interval=1000; timer.Tick += delegate { Check(); }; timer.Start();
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if(cycle.Transitioning){e.Cancel=true;return;}
            if (active && !closing) { EndTrial(); if(active) { e.Cancel=true; return; } }
            timer.Stop(); if(viewer!=null) viewer.Dispose(); lease.Dispose();
        };
    }
    void Record(string phase) {
        string text="phase="+phase+"\noriginal_enabled="+originalEnabled+"\noriginal_service_stopped="+originalServiceStopped+"\nowned_session="+(owned.HasValue?owned.Value.ToString():"unknown")+"\n";
        // Publish only after checking the actual client settings, never a GUI checkbox.
        if(phase=="connected" && rdp!=null && rdp.Connected==1) {
            rdp.ValidatePrepared();
            text+="input_isolation=verified-v1\n";
        }
        text+="published_unix="+(DateTime.UtcNow-new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalSeconds.ToString("R",System.Globalization.CultureInfo.InvariantCulture)+"\n";
        using(var process=System.Diagnostics.Process.GetCurrentProcess()) {
            text+="parent_session="+process.SessionId+"\nparent_pid="+process.Id+"\nparent_started="+(process.StartTime.ToUniversalTime()-new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalSeconds.ToString("R",System.Globalization.CultureInfo.InvariantCulture)+"\n";
        }
        string temp=journal+".new"; File.WriteAllText(temp,text,Encoding.UTF8);
        if(File.Exists(journal)) File.Replace(temp,journal,null); else File.Move(temp,journal);
    }
    void Begin() {
        if(active || cycle.Transitioning || File.Exists(journal)) return;
        if(!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator)) {
            status.Text="未启用：请关闭此面板，以管理员身份运行验证入口。程序不会自动提权。"; return;
        }
        if(!cycle.EnterTransition()) return;
        timer.Stop();
        string failure=null;
        try {
            cycle.ResetForStart();
            heartbeatRun=null;heartbeatTicks=0;
            started=DateTime.MinValue;
            // A completed cycle must never expose its disposed COM control again.
            if(rdp!=null){rdp.Dispose();rdp=null;}
            if(Native.Child().HasValue) throw new InvalidOperationException("已有子会话，拒绝接管或注销。");
            RdpHost.Stage="读取初始系统状态";
            originalEnabled=Native.Enabled();
            using(ServiceController service=new ServiceController("TermService")) originalServiceStopped=service.Status==ServiceControllerStatus.Stopped;
            Record("prepared"); active=true; start.Enabled=false;
            viewer=new Form {Text="天意Bot · 分身画面（关闭此窗口只隐藏）",Size=new Size(1040,810)};
            rdp=new RdpHost {Dock=DockStyle.Fill}; viewer.Controls.Add(rdp);
            viewer.FormClosing += delegate(object sender,FormClosingEventArgs e) {if(!closing){e.Cancel=true;viewer.Hide();}};
            viewer.Show(); rdp.CreateControl();
            rdp.PrepareLocal();
            RdpHost.Stage="启用子会话";
            if(!originalEnabled) Native.Enable(true);
            Record("enabled");
            // Recheck immediately before connect. Never attach to a known existing child.
            if(Native.Child().HasValue) {uncertain=true;throw new InvalidOperationException("连接前出现其他子会话，停止。");}
            started=DateTime.UtcNow; Record("connecting"); rdp.ConnectPrepared();
            status.Text="正在连接；最多等待30秒，不自动重试。";
        } catch(Exception ex) {
            failure="创建失败："+ErrorDetail(ex);
            File.WriteAllText(Path.Combine(Path.GetDirectoryName(journal),"last-error.txt"),failure,Encoding.UTF8);
        } finally {
            cycle.LeaveTransition(); timer.Start();
        }
        if(failure!=null){status.Text=failure; if(active) EndTrial();}
    }
    void Check() {
        if(!cycle.CanPoll || rdp==null || checking) return;
        checking=true;
        try {
            uint? current=Native.Child();
            if(owned.HasValue && current!=owned) {uncertain=true;Record("identity_changed");status.Text="子会话身份改变，禁止自动注销或恢复；保留记录。";return;}
            if(rdp.Connected==1 && current.HasValue) {
                if(!owned.HasValue)owned=current;
                Record("connected"); // Publish only after native child identity and RDP checks.
                cycle.QueryFailed=false;
                status.Text="RDP已连接；分身"+(viewer.Visible?"可见":"已隐藏")+"。\n请在分身内打开“分身内打开微信管理.vbs”连接小号；微信后台收发尚未验证。";
            } else if(!owned.HasValue && (DateTime.UtcNow-started).TotalSeconds>30) {
                status.Text="连接超时；不自动重试。"; EndTrial();
            } else if(owned.HasValue) {Record("disconnected");status.Text="连接已断开；不会自动重连。请结束本次测试。";}
        } catch(Exception ex) {
            // A failed read is not proof of changed ownership. Continue read-only
            // polling, but publish no usable lease and forbid logoff until a
            // full native identity + RDP isolation check succeeds again.
            cycle.QueryFailed=true;
            status.Text="状态查询失败，机器人已暂停；正在重查连接，不会重连、注销或修改系统。";
            try {File.WriteAllText(Path.Combine(Path.GetDirectoryName(journal),"query-error.txt"),ErrorDetail(ex),Encoding.UTF8);} catch(Exception) {}
            try {Record("query_failed");} catch(Exception) {}
        }
        finally {checking=false;}
    }
    async void EndTrial() {
        if(!active || !cycle.EnterTransition()) return;
        timer.Stop();
        try {
            if(uncertain || cycle.QueryFailed) throw new InvalidOperationException("归属不确定，不能安全注销。恢复记录："+journal);
            uint? current=Native.Child();
            if(current.HasValue && current!=owned) throw new InvalidOperationException("出现未确认归属的子会话，不能注销。");
            if(owned.HasValue && current==owned) {
                if(MessageBox.Show("将注销本次分身，其中程序会关闭，未保存内容会丢失。是否继续？","结束空白测试",MessageBoxButtons.YesNo)!=DialogResult.Yes) return;
                // Logoff can pump messages or take seconds. Never block the UI waiting
                // for completion and never let a normal poll observe deliberate teardown.
                uint target=owned.Value;
                status.Text="正在结束本次分身，请稍候；不会重复注销。";
                Record("ending");
                if(!await Task.Run(()=>Native.WTSLogoffSession(IntPtr.Zero,target,false))) throw new InvalidOperationException("注销失败，保留恢复记录。");
                DateTime deadline=DateTime.UtcNow.AddSeconds(25);
                while(Native.Child()==target && DateTime.UtcNow<deadline) await Task.Delay(250);
            }
            if(rdp!=null) rdp.DisconnectLocal();
            if(Native.Child().HasValue) throw new InvalidOperationException("子会话尚未退出，暂不恢复开关。");
            if(Native.Enabled()!=originalEnabled) Native.Enable(originalEnabled);
            closing=true;
            try { if(viewer!=null){viewer.Close();viewer.Dispose();viewer=null;} rdp=null; }
            finally {closing=false;}
            Record("switch_restored");
            string completed=journal+".completed";
            if(File.Exists(completed)) File.Move(completed,completed+"."+Guid.NewGuid().ToString("N"));
            File.Move(journal,completed);
            cycle.Complete(); started=DateTime.MinValue;
            start.Enabled=true;
            status.Text="已确认没有子会话，开关已恢复。"+(originalServiceStopped?"TermService原为停止；未强停共享服务，请核查其是否自动停止。":"");
        } catch(Exception ex){status.Text="恢复未完成："+ex.Message;Record("recovery_pending");}
        finally {cycle.LeaveTransition();if(!IsDisposed)timer.Start();}
    }
    [STAThread] static void Main(string[] args) {
        Application.EnableVisualStyles();
        if(args.Length==2 && args[0]=="--heartbeat-selftest") {
            string path=Path.Combine(args[1],"heartbeat-fixture.txt");
            try {
                string run=Guid.NewGuid().ToString("N"), baseline=null;long count=0;
                File.WriteAllText(path,"7\n"+run+"\n1\n"+DateTime.UtcNow.Ticks);
                if(!HeartbeatProbe.Read(7,true,ref baseline,ref count,path).Contains("新增 0"))throw new Exception("baseline");
                File.WriteAllText(path,"7\n"+run+"\n4\n"+DateTime.UtcNow.Ticks);
                if(!HeartbeatProbe.Read(7,true,ref baseline,ref count,path).Contains("新增 3"))throw new Exception("progress");
                if(!HeartbeatProbe.Read(8,true,ref baseline,ref count,path).Contains("尚无"))throw new Exception("identity");
                File.WriteAllText(path,"7\n"+run+"\n4\n"+DateTime.UtcNow.AddSeconds(-10).Ticks);
                if(!HeartbeatProbe.Read(7,true,ref baseline,ref count,path).Contains("过期"))throw new Exception("stale");
                File.WriteAllText(path,"invalid");
                if(!HeartbeatProbe.Read(7,true,ref baseline,ref count,path).Contains("尚无"))throw new Exception("malformed");
                File.WriteAllText(Path.Combine(args[1],"result.txt"),"heartbeat_reader_passed; simulated_reports_only");
            }catch(Exception){Environment.ExitCode=1;}
            return;
        }
        if(args.Length==2 && args[0]=="--cycle-selftest") {
            try {
                CycleState state=new CycleState();
                for(uint round=1;round<=2;round++) {
                    if(!state.EnterTransition())throw new Exception("start transition rejected");
                    state.ResetForStart(); state.Active=true;
                    if(state.CanPoll || state.EnterTransition())throw new Exception("start reentry allowed");
                    state.LeaveTransition(); state.Owned=round;
                    if(!state.CanPoll)throw new Exception("poll disabled after connection");
                    state.QueryFailed=true;
                    if(!state.CanPoll)throw new Exception("query failure must allow readonly recheck");
                    state.Uncertain=true;
                    if(state.CanPoll)throw new Exception("changed identity must stay blocked");
                    state.Uncertain=false;state.QueryFailed=false;
                    if(!state.EnterTransition() || state.CanPoll || state.EnterTransition())throw new Exception("end reentry allowed");
                    state.Complete(); state.LeaveTransition();
                    if(state.Active || state.Uncertain || state.Owned.HasValue || state.CanPoll)throw new Exception("dirty completion");
                }
                state.Uncertain=true;state.Owned=99;state.ResetForStart();
                if(state.Uncertain || state.Owned.HasValue)throw new Exception("stale flags retained");
                state.Active=true;bool rejected=false;
                try{state.ResetForStart();}catch(InvalidOperationException){rejected=true;}
                if(!rejected)throw new Exception("active reset allowed");
                File.WriteAllText(args[1],"two_cycles_and_reentry_passed; simulated_state_only");
            }catch(Exception ex){File.WriteAllText(args[1],ex.Message);Environment.ExitCode=1;}
            return;
        }
        if(args.Length==2 && args[0]=="--preflight") {
            // Software QA: only a new, disconnected in-process ActiveX instance.
            // No system switch, existing window, Connect, credentials or session APIs.
            string result;
            try {
                using(Form form=new ProbeForm()) using(RdpHost host=new RdpHost()) {
                    form.Controls.Add(host); form.Show(); host.CreateControl();
                    host.PrepareLocal(); result="prepared_without_connection";
                }
            } catch(Exception ex) {result=ErrorDetail(ex);Environment.ExitCode=1;}
            File.WriteAllText(args[1],result,Encoding.UTF8); return;
        }
        try {
            uint current=(uint)System.Diagnostics.Process.GetCurrentProcess().SessionId;
            string record=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"TianyiBotSessionTrial","recovery.txt");
            if(File.Exists(record) && new FileInfo(record).Length<=4096 && Array.IndexOf(File.ReadAllLines(record),"owned_session="+current)>=0) {
                MessageBox.Show("这里是分身。请打开项目src目录中的“分身内打开微信管理.vbs”，不需要再次运行控制器。","天意Bot");
                return;
            }
            TrialForm form=new TrialForm();
            if(args.Length==1 && args[0]=="--open-child")form.Shown+=delegate {form.Begin();};
            Application.Run(form);
        }
        catch(Exception ex){MessageBox.Show("验证入口未完成："+ex.GetType().Name+"。如有恢复记录请保留；未自动重试。","天意Bot");}
    }
}
