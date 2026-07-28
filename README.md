# ClipVault

ClipVault 是一个本地运行的 Windows 剪贴板管理工具。它会记录复制过的文本、链接和图片，让你可以用全局快捷键快速打开历史列表，再用键盘选择并粘贴回刚才的输入位置。

它还支持把当前剪贴板内容一键保存为 Obsidian Markdown 笔记，适合把聊天、网页、截图和临时资料沉淀到自己的知识库里。

## 功能

- 本地保存剪贴板历史：文本、链接、图片。
- 全局快捷键打开剪贴板窗口。
- 打开后默认选中最新复制内容，可直接按上下键选择。
- `Enter` 或双击记录后，粘贴回打开前的输入窗口。
- 支持复制、编辑、置顶、删除历史记录。
- 支持搜索内容、来源应用、日期范围和内容类型。
- 支持智能分类：代码、待办、提示词、联系方式、文件路径、敏感内容。
- 支持一键推送当前剪贴板到 Obsidian 文件夹。
- 两个快捷键都可以在设置页录制修改。
- 支持暂停记录、敏感内容过滤、排除应用、历史保留周期和重复清理。
- Windows 托盘常驻，关闭窗口默认隐藏到托盘。
- 启动时会尝试注册当前用户开机自启动。

## 当前默认快捷键

| 操作 | 默认快捷键 |
| --- | --- |
| 打开 ClipVault | `Ctrl+Alt+C` |
| 推送当前剪贴板到 Obsidian | `Ctrl+Alt+O` |
| 上下选择记录 | `↑` / `↓` |
| 粘贴选中记录 | `Enter` |
| 收起窗口 | `Esc` |

快捷键可以在应用设置中修改。实际生效快捷键以设置页显示为准。

## 安装运行

### 方式一：直接运行发布版

直接下载：[ClipVault.exe](https://github.com/amirage7/ClipVault/releases/latest/download/ClipVault.exe)。下载后双击运行即可。

也可以在 [Releases 页面](https://github.com/amirage7/ClipVault/releases) 查看历史版本和发布说明。

首次运行后，应用会：

- 在 `%LOCALAPPDATA%\ClipVault\data` 保存配置和剪贴板历史。
- 尝试注册开机自启动。
- 在系统托盘常驻运行。

### 方式二：从源码运行

要求：

- Windows 10/11
- Python 3.11 或更新版本

安装依赖：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r app\requirements.txt
```

启动：

```powershell
.venv\Scripts\python.exe run.py
```

也可以双击：

```text
start.bat
```

## 打包

项目使用 PyInstaller 打包为单文件 Windows 程序。

```powershell
build.bat
```

打包完成后，可执行文件会输出到：

```text
release\ClipVault.exe
```

## Obsidian 推送

在设置页选择一个 Obsidian 目标文件夹后，可以按 Obsidian 推送快捷键，把当前系统剪贴板保存成 Markdown 文件。

保存规则：

- 文本：写入 Markdown 正文。
- 链接：写成 Markdown 链接。
- 图片：复制到目标文件夹下的 `attachments/`，并在笔记中插入 Obsidian 图片引用。

如果没有设置目标文件夹，推送会失败并显示通知。

## 数据与隐私

ClipVault 当前是本地应用，不依赖云端账号，也不会主动上传剪贴板内容。

发布版本地数据默认保存在 `%LOCALAPPDATA%\ClipVault\data`；从源码运行时仍保存在项目目录的 `data/`。

| 内容 | 路径 |
| --- | --- |
| 配置 | `%LOCALAPPDATA%\ClipVault\data\config.json` |
| 剪贴板数据库 | `%LOCALAPPDATA%\ClipVault\data\clipboard.db` |
| 图片文件 | `%LOCALAPPDATA%\ClipVault\data\images\` |
| 日志 | `%LOCALAPPDATA%\ClipVault\data\app.log` |

从旧版本升级时，ClipVault 首次启动会自动迁移同级 `data` 目录中的历史、图片和设置。迁移只复制，不会删除旧数据，也不会覆盖已有的新数据。

注意：

- `data/` 包含个人剪贴板历史，已经在 `.gitignore` 中排除。
- `exports/` 可能包含导出的剪贴板内容，也已经排除。
- 发布源码前不要手动提交 `data/`、`exports/`、`release/`、虚拟环境或构建目录。

## 开发

运行测试：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

项目结构：

```text
app/
  static/          前端界面
  server.py        Flask API 和静态服务
  clipboard.py     剪贴板读取、写回和监听
  db.py            SQLite 存储
  hotkey.py        Windows 全局快捷键
  obsidian.py      Obsidian Markdown 导出
  autostart.py     开机自启动
run.py             桌面窗口、托盘、快捷键和应用入口
tests/             单元测试
scripts/           辅助脚本
```

## 常见问题

### 快捷键没有反应

先确认 ClipVault 正在运行，并检查系统托盘。然后打开设置页查看快捷键注册状态。如果快捷键被其他软件占用，重新录制一个组合键并保存。

### 复制内容没有保存

检查是否开启了“暂停记录”，或是否把当前应用加入了“不记录这些应用”。如果内容疑似密码、验证码或 Token，敏感内容过滤也可能会跳过保存。

### Obsidian 没有收到内容

确认设置页已经选择 Obsidian 目标文件夹，并且当前系统剪贴板确实有内容。失败原因通常会通过系统通知或 `data/app.log` 提示。

### 关闭窗口后程序是不是退出了

不是。关闭按钮默认只是隐藏到托盘。需要真正退出时，请从系统托盘菜单选择“退出”。

## 许可

当前仓库尚未附带开源许可证。没有许可证时，默认保留所有权利；如果需要允许别人自由使用、修改和分发，请添加合适的 LICENSE 文件。
