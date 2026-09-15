# Design and plan

## Current priority: local multi-window targeting, VM path deferred

User now prioritizes no VM and explicit target selection. Proposed local controller/GUI with no LAN listener needed; retain core and adapter boundary. Do not yet enable live host mode. Proof sequence: window selection and read-only binding; two-account simulated ownership tests; only then explicitly authorized real account/group tests on host WeChat4.1.13.12 (still not proven compatible).

Target contract: user selects/highlights an exact main HWND and manually confirms the small account; bind HWND + PID + process creation time + selection generation. Never default to first window/title/nickname. Scope group windows, callback messages, preview and popup menus to the selected process and verified ownership/context; record bound chat HWND as well as exact group. Require unique match; ambiguous/shared-PID or changed-window cases fail closed rather than guessing, following process ancestry alone is insufficient account proof. Same-process account switching remains manual limitation; stop/rebind when changing accounts. Validate before any side effect, including cleanup/Esc. No fallback global WeChat construction.

Pinned source audit found: WeChat accepts hwnd; get_all_sub_wnds filters PID, BUT open_separate_window's newly-opened branch calls WeChatSubWnd by name and that constructor uses global FindWindow. Menu scans global class/name with no PID; WeChatImage calls find_window_from_root without its available pid argument. Existing app group check validates names/types only, insufficient for identical group names across accounts. These paths require adapter-contained repair before claiming no cross-account operation.

Local shared desktop cannot offer VM-style input isolation: ChatBox uses focus/click/clipboard paste; app preview also clears clipboard. Proposed conservative pause-on-user-activity policy and explicit send-operation mode need validation and cannot eliminate race windows or restore arbitrary clipboard formats reliably. No claim of zero interference, locked-desktop operation or stable account-ID recognition. This turn documents the plan only; actual host backend switching remains pending.

## VM/GUI extension

Host inspection: Windows 11 Home China 25H2 build26200, i7-14650HX 16C/24T, VT-x+SLAT enabled, no hypervisor present, ~16GB RAM/~3.5GB currently free; C ~337GiB free, D ~18.8GiB free. No VirtualBox/VMware/Hyper-V installed. Proposed VirtualBox7.2 base, Windows11 supported install media from Microsoft (user-owned license), 2vCPU/4GiB/64GiB dynamic disk on C; estimates, not measurements. Host should free RAM first. No system setup performed.

Host Tkinter/ttk app -> TLS pinned certificate + bearer secret -> stdlib threaded HTTP management listener on guest host-only IP + exact host source allowlist. NAT NIC for WeChat outgoing internet, host-only NIC for management, no bridge/forwarding. No browser frontend/cloud. Guest service runs in interactive user session; never Windows service session0. GUI disconnection has no lifecycle effect. Normal setup/config through host/guest Tk windows. Secrets generated and stored in user-local directories; pairing export is sensitive and never logged. API IDs select images; arbitrary paths/commands unavailable.

Guest controller state machine owns one bot thread and explicit connection/binding/start/stop states. Native UI work runs only in a detected VirtualBox guest; simulation is explicit and never imports wxauto. Probe window candidates, bind an exact hwnd+PID+process-start identity and a human-entered small-account label; revalidate before sending and while running. Source does not expose stable account ID: labels are manual attestation, not automatic identity verification. Login/account changes invalidate on window/process loss; same-process account switch cannot be guaranteed detected and is forbidden while running. Fresh confirmation at each Start. Require desktop active; lock/RDP/session loss invalidates binding and stops automation. No automatic restart after unsafe session loss.

Reuse GalleryBot/core; add stoppable adapter lifecycle and readiness callback. Gallery reads use independent read-only SQLite connections; thumbnails decoded and bounded server-side. Client network operations use one background executor and Tk.after result handoff; never block UI on HTTP. A disconnected GUI marks all live state unknown, and does not auto-replay commands.

Implementation validation: HTTPS loopback server with simulated guest, real Tk widget tests and rendering, account/stop/config/network tests, original regression suite. These prove local software only; VM console detach, guest screen rendering, current client compatibility, actual account identity and no host input interference require user-approved VM/live acceptance.

Use Python 3.11–3.13 (develop with bundled 3.12 x64), SQLite from the standard library, Pillow for validating image bytes, and optional FreeWisdom/wxauto-4.0 pinned to bd7c5233e79c0a185638a325bf6d30607244dfa8. Use unittest so offline tests need no WeChat or separate test framework.

Normalized immutable MessageEvent -> application orchestration -> parser, pending service, gallery service -> SQLite + content-addressed image files. Adapter owns all wxauto types and native message handles. Download images lazily only for a matching pending operation; never attach native objects to the event.

Single application lock serializes callbacks and SQLite access; adapter serializes its UI operations. One process per data directory is enforced at startup. Pending deadlines use receipt monotonic time, not WeChat's sparse display time. A second add command replaces that sender's pending keyword. Unrelated text leaves pending intact. At expiry an image gets a friendly timeout response. After a download/storage failure, pending remains until deadline for a new image.

Files use SHA-256 names and validated format extensions under data/images. Copy to a temporary file, flush, then atomic rename before committing metadata; failed metadata commit may leave a safe unreferenced file, never a committed row pointing to a partial copy. Duplicate scope is (namespace, keyword, sha256). Last successful selection is persisted separately. Repository resolves all stored paths beneath images and rejects escape. Back up the whole stopped data directory for recovery.

Group key is configured exact chat name. Sender key is wxauto's documented sender display name: group members must have distinct stable display names. This backend does not promise immutable WeChat IDs; document collision/rename limitations. Unknown/generic sender labels are ignored. Event dedup uses UI message id + group with bounded lifetime/capacity; do not use content/hash-only dedup because legitimate identical text/images can recur.

FreeWisdom integration: one listener callback worker preserves submission order. Check ChatInfo for exact group name/type before dispatch and sending. Free ImageMessage lacks download(); use its click/roll_into_view, the existing WeChatImage preview, Menu copy and clipboard file paths to copy into a private temporary directory. Do not use upstream preview.save because it can delete an empty source file. Disable upstream wx.delete_update_files before constructing WeChat (otherwise startup and every listen cycle delete the client's update cache). Disable backend file/debug logs and sender OCR fallback; ambiguous senders are ignored. Pin package origin via installed direct_url metadata so unrelated PyPI wxauto4 is rejected. These patches stay within the thin adapter.

Implementation order: core and fake adapter with tests; pinned API adapter with contract doubles; config and safe CLI; packaging and offline demo; self review and regression checks; delivery acceptance; closure/export after actual acceptance. No model-selection gate. The local development scope is already authorized.
# 本机窗口绑定第一阶段：0.3实现

`management/window_binding.py`独立于wxauto：白名单进程名，UIA仅访问顶层ClassName与ProcessId，mmui::MainWindow才成为候选；不读Name、不遍历聊天。读取exe、进程创建时间、HWND、最小化和owner元数据，全部留在内存。扫描在8秒超时的隐藏子进程中运行，异常不输出敏感原始信息。

BindingService通过注入provider验证；刷新增加代次并取消旧绑定。绑定复扫，检查唯一完整身份及唯一进程主窗口；之后GUI每2秒排程检查。扫描最多8秒，检测不是瞬时保证；短暂锁屏或同HWND同进程内换号可能无法识别。超过15秒的检查间隙要求重新确认。

LocalBindingWindow为实际Tk界面：空选择启动、异步扫描、5秒后只读GetForegroundWindow辅助定位、复选确认、解绑、状态。用户自行切换微信窗口，程序不调用激活/点击/键盘/剪贴板API。无启动机器人入口、无本地HTTP接口、无账号/绑定持久化。主机默认打开此界面，旧远程ManagerWindow保留为显式--remote兼容入口。

此模块仅为未来全链路隔离提供窗口选择基础，不能直接授权旧适配器在主机执行。群子窗、菜单和图片预览的全局查找仍需后续修复及单独测试。
# 0.4：全后台硬约束及当前未完成边界

机器人禁止前台/鼠标/焦点/按键/全局剪贴板兜底。`window_scope.require_verified_background_backend`始终拒绝当前真实启动，无环境变量或勾选覆盖；LocalWorkspace与旧GuestController真实路径都在导入/启动实时后端之前调用。

`WindowScope`为纯策略，无UI动作：主窗身份包含进程创建时间和提供方实例标识；所选群必须allowlist且唯一，预览/菜单要求本次操作新出现及直接owner正确；所有owner链必须回到唯一绑定主窗。无owner、重复窗口、PID重用、循环链或失效后锁死会话。真实WeChat可能不提供可验证owner链，所以当前仅模拟验证，未接入任何真实收发。

`background_probe`隐藏子进程12秒截止，输入绑定Window（stdin而非命令行），前后只读复核绑定，COM线程上下文中限定到该root的ChatMessagePage/XSplitterView/ChatInputField，只读Exists和IsValuePatternAvailable（30043）。不读取Name/Value或调用Invoke/SetValue，不导入wxauto，不以接口存在推断可发送。UI明确所有真实能力仍未验证。

`LocalWorkspace`持有OS数据锁，复用GuestController配置校验、SQLite图库和缩略图。`gallery`与`offline-demo`分开；演示采用FakeAdapter和有界事件队列、真实GalleryBot核心，记录仅计数，启动/停止/清理队列；关闭释放锁，失败不假装退出。`LocalOperationsWindow`为异步Tk界面，关闭控制窗口停止演示，不修改原GUI绑定或旧数据，不提供真实启动后门。
# 0.4.1草稿诊断设计

`draft_trial`仅作为用户触发的本机应用功能。GUI取得当前绑定Window，固定组名同学群，重置单次勾选并暂时禁用父窗口重新绑定；隐藏子进程用stdin接收范围，15秒总超时，无自动重试。默认启动/导入/只读检查均不调用实验。

目标验证：复用只读主窗/进程创建时间检查，限定根控件；ChatMessagePage内ChatInfoView标题完整匹配同学群且存在群人数标识，输入控件PID/根归属/RuntimeId稳定、Enabled、ValuePattern非只读；初次输入框有子控件即保守拒绝，防止覆盖不明附件。只在内存读取草稿值，不记日志。群标识检测来自固定源码结构，不代表4.1.13.12真实兼容已验证。

实验状态机为初始空值→最多一次唯一标记SetValue→读取核对→只清除同一目标内完全匹配的本次标记→核对空值。Value调用抛错可能已产生副作用，仍仅在身份/环境有效时尝试清理本次标记，不重写；如果环境改变，不再执行清理动作，报告可能残留。后台接口使用[Microsoft ValuePattern.SetValue](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationvaluepattern-setvalue)，不调用SendKeys/Click/Invoke等替代路径。

环境监视在独立线程每10ms只读采样GetForegroundWindow、GetCursorPos、GetLastInputInfo（只观察本会话输入时间，不读取按键内容）。检测到任何变化会锁定失败；不能保证捕获短暂变化或归因于微信。超时子进程退出不能保证远端COM已取消，因此结果unknown、人工检查草稿、禁止自动重试。只保存安全结果字段，不保存Window/草稿/账号。
# 0.4.2补充：可定位的只读前置诊断

NativeDraftDriver仅使用标准UIA接口，以GetParentControl/GetRuntimeId和ProcessId验证输入框属于绑定主窗，限制64层并拒绝循环。禁止依赖wxauto的矩形位置扩展判断归属。前置检查逐步记录白名单阶段、异常类型及可选HRESULT，不保存异常原文、草稿或身份。

独立read_only子进程路径只调用verify/read，不能进入experiment或SetValue；结果写入last-draft-diagnostic.json。GUI绑定在等待期间改变则丢弃结果；诊断通过也不启用机器人。
# 0.5独立子会话试验宿主

Python管理器通过系统Framework64 C#编译器构建小型WinForms/AxHost程序，源码随Python包提供，按源码哈希缓存到用户目录。借鉴BetterGI的职责划分，独立编写实现，不复制其代码或资源。使用Microsoft文档的WTSEnableChildSessions及RDP ActiveX ConnectToChildSession；localhost固定目标，禁用剪贴板/磁盘/设备/打印机重定向和自动重连，无凭据收集、输入注入或业务进程启动。

独立控制器与可隐藏画面窗口分离；每秒显示连接状态，30秒连接超时不重试。写前原子记录原始状态；存在旧恢复记录禁止新建。本工具文件锁只能协调本工具，不能消除其他子会话客户端竞争；创建前后二次检查及退出确认降低误注销风险，异常归属时停止并保留日志，不自动接管。

退出请求仅注销本次记录的会话，经用户确认后执行，复核无子会话才恢复开关。TermService是共享服务，原为停止时只报告待核查，不直接强停。崩溃/强杀后不承诺自动恢复；恢复记录在LocalAppData/TianyiBotSessionTrial/recovery.txt，需要人工核查。锁屏、休眠、后台输入及微信兼容均未验证。

参考：https://github.com/babalae/better-genshin-impact/tree/main/BetterGenshinImpact/Service/ChildSession
https://learn.microsoft.com/en-us/windows/win32/termserv/child-sessions
