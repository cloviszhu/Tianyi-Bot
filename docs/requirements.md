# 天意Bot MVP

## 0.4.1授权边界

用户明确指定“同学群；确认授权”，范围为已人工绑定小号的后台草稿写入后清除、不发送。后续“继续”允许完成对应本地测试入口，但不扩张为消息发送、监听、图片或其他群操作。测试由用户在项目GUI主动触发；本轮agent只做替身/本地Tk验证。

不得自动打开群或激活微信；只在当前主窗口可确认同学群、群聊标识、空草稿、输入控件唯一绑定身份时尝试。已有草稿/附件不覆盖。环境变化或结果不明时禁止重试，必要时交用户人工清理本次标记。通过也不放开真实机器人。

## 当前最高优先级约束：机器人必须全后台（用户本轮补充）

用户明确“所有操作都必须支持在后台进行，不能强制要求前台也不能占用我的鼠标”，随后澄清指机器人操作。不得要求用户接受前台兜底、短暂抢焦点、模拟鼠标或按键；保留原有不争用剪贴板的目标。管理器交互及人工账号核对不属于机器人收发。

此约束取代此前“无VM需要接受输入干扰”的取舍。任何真实启动必须等到后台能力实现与验证，当前禁用，不能用勾选同意干扰绕过。源码证明现免费库路径不满足，并不证明所有自研方式均不可能；UIA接口声明不等于可用或无副作用，后台图片路径尤其未知。

当前可交付本机配置、图库、独立离线演示、只读结构能力检查及窗口所有者链策略。只读检查由用户对已绑定窗口主动触发，只查控件类和属性，不读消息/账号值、不执行操作。实收发需之后明确账号、群和授权。项目保持开放，全后台真实机器人尚未完成。

## 最新方向：优先同机非虚拟机（取代VM优先）

用户要求优先不用虚拟机、清理本次大体积下载，核心问题是多开后明确指定监听/操作哪个微信窗口。本轮授权清理安装介质及研究方案，不视为真实账号/群收发授权。当前主机真实运行保护保留，直到全链路窗口隔离改造和测试完成；不能简单移除VirtualBox检测即可上线。无VM方案不能承诺与主号同时操作时完全不争用焦点/鼠标/剪贴板，该取舍需在真实启用前明确。既有图库/GUI复用，项目不归档。

## 当前扩展：主机桌面GUI＋隔离虚拟机（2026-09-14）

用户明确要求暂不关闭归档。主机正常使用主号；小号、自动化、图库与SQLite放在轻量Windows虚拟机；主机提供可实际操作的Python桌面GUI，日常不编辑.env或输入命令。

实现服务在线/真实状态、窗口选择与人工小号确认、群/超时/图片限制配置、启动/停止/重连、图库列表/计数/缩略图和错误状态。GUI断开不停止guest，重连重新获取状态。管理通信只在本机/host-only网络，认证、加密、限制源地址、无任意路径/命令接口。保留免费适配器与副作用限制。

先调查硬件与UI会话要求，准备系统变更方案及恢复办法。仅本地开发及模拟/本地网络测试获授权；虚拟化安装、网卡/防火墙/系统策略、镜像下载及真实账号操作未经授权。不得自动登录、降级主机微信或购买软件。免费后端4.0.5与本机4.1.13.12不宣称兼容。账户ID不可可靠读取时必须展示人工绑定限制，不伪装自动识别。

新增验证：桌面控件操作、管理接口认证/证书/源IP/路径边界、断线重连、GUI关闭后guest继续、绑定失效/多窗口、启动停止/桌面丢失、配置持久化、图片预览。真实VM与微信/输入隔离测试明确留待授权，不用模拟代替。

Source: user attachment 2119b5b9-175d-414a-b4b9-feac5cd503e4/pasted-text.txt.

Build a small local Python image-library bot for ordinary WeChat groups on Windows 11.
No AI, server, cloud storage, injection, or custom WeChat protocol.

## 本机第一阶段实施范围（用户“开始”授权）

交付独立、实际可用的Tk窗口识别与绑定界面，替换默认管理器入口。只读顶层结构，不读取聊天控件、账号名称或剪贴板；不调用wxauto、不启动监听或发送。提供用户主动切换后的前台窗口选择，禁止默认选第一窗口。

身份由HWND、PID、进程创建时间、可执行文件路径及扫描代次组成；每次绑定重新扫描验证，之后周期复核。同一进程多个主窗口、最小化/非独立窗口、结构变化或检测异常拒绝/取消绑定，不自动替换。人工确认不等于可靠账号识别，同窗口换号必须人工解绑。该阶段不包含真实多账号收发与无输入干扰验收。

## Acceptance criteria

- `/加图 <keyword>` awaits the next image from the same sender in the same group for 60 seconds. Text and image are separate events.
- `/取消` cancels only that sender/group's pending add; `/帮助` explains usage.
- Trim surrounding whitespace; match keywords exactly. Unknown normal text is ignored.
- Select randomly; exclude the last successfully sent image when alternatives exist.
- SQLite metadata and disk images persist across restart; namespaces are per group.
- SHA-256 deduplicates within a gallery. User text never determines a filesystem path.
- Ignore self messages and practically deduplicate repeated event IDs without suppressing legitimate repeated text.
- Friendly errors, safe file/database failure handling, no sensitive message/account logging.
- Core and synthetic adapter work without WeChat. Tests include parsing, pending isolation, expiry, insertion, duplicates, exact lookup, selection, self filtering, paths, and persistence.
- Real adapter uses researched wxauto 4.x APIs and configured group allowlist. User follow-up prefers free FreeWisdom/wxauto-4.0 over paid wxautox4. Document precise API and compatibility evidence and limits.
- Fresh install, startup instructions, offline demonstration, self-review and recovery guidance.

## Authorization and boundaries

User authorized implementation and mock development verification. No live account or group operations have been authorized. Live compatibility is a separate unperformed check, never represented by mock results. Do not install/activate a paid license or change the WeChat client. Final workflow acceptance requires a new actual user answer.
# 0.4.2诊断要求

失败必须区分绑定、群结构、输入框归属、Value接口和草稿读取等步骤；允许用户单独运行不写入的前置诊断，不要求再次授权写入。不得将旧not_run报告解释成已验证后台能力，不得用同进程但不同根窗口的输入框继续操作。
# 0.5空白子会话验证范围

用户授权参考BetterGI代码独立实现空白分身入口，而非操作BetterGI或安装其副本。仅创建/显示/隐藏/检查/结束本次本地子会话；不启动微信、游戏、机器人或执行输入测试。系统自启动程序不受本工具控制，子会话不是账号/文件安全沙箱。真实收发禁止不变。

面板默认不提权、不启用、不连接；管理员创建按钮才执行系统变更。保存原始子会话开关和服务状态，拒绝已有子会话、残留恢复记录及本工具多实例。恢复只针对本次确认的会话；不强停共享TermService，不修改普通RDP设置、防火墙或安全软件。
