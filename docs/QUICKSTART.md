# 五分钟装上这门课

面向**完全没用过命令行**的同学。照着做就行，不用理解每一步为什么。

---

## 一、你需要什么

一台电脑，装了 **Linux**（推荐 Ubuntu）或 **macOS**。

- 只要 **Python 3.10 以上**。Ubuntu 24.04 和 macOS 自带，通常不用装。
- **不需要**联网之外的东西：不需要装数据库、不需要 Docker、不需要 pip 装包。
- 课程**只用 Python 标准库**，所以不会出现"依赖装不上"的经典问题。

> Windows 用户：请先装 **WSL2（Ubuntu）**，再按下面的步骤做。
> 在 WSL 里和在 Ubuntu 里完全一样。见文末《Windows 怎么办》。

---

## 二、装（复制一行，粘贴，回车）

打开「终端」（Ubuntu 里叫 Terminal，macOS 里在「启动台 → 其他 → 终端」），
把下面**这一整行**复制进去，按回车：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/tob124/motor-control-tutorial/main/deploy.sh)
```

然后它会自己做完所有事。你会看到一串带 ✓ 的提示，例如：

```
[1] 检查你的电脑环境
    ✓ Python 3.12.3
    ✓ 端口 8770 空闲
[2] 把课程放到你的电脑上
    ✓ 下载完成：/home/你的用户名/robocon-control-course
[3] 生成课程内容
    ✓ 课程内容已生成：11 周 / 44 个单元
[4] 自检验证
    ✓ 课程服务可以正常启动
```

最后它会问一句「现在启动课程吗？」，直接按回车。

浏览器会自动打开课程页面。如果没自动打开，手动访问：

```
http://127.0.0.1:8770/
```

**装好了。** 就这么简单。

---

## 三、以后每次用

启动课程（在终端里）：

```bash
cd ~/robocon-control-course
./start.sh
```

然后浏览器访问 <http://127.0.0.1:8770/>。

**停止课程**：在运行课程的终端窗口里按 `Ctrl` + `C`。

> 提示：终端窗口要一直开着，课程才能访问。关掉窗口就等于关掉课程。
> 这不影响你的学习记录——它保存在硬盘上，下次打开还在。

---

## 四、常见问题

**Q：提示"端口被占用"怎么办？**
换个端口：

```bash
./start.sh --port 8790
```

然后访问 <http://127.0.0.1:8790/>。

**Q：提示"没有找到 Python"怎么办？**
Ubuntu 上执行：

```bash
sudo apt update && sudo apt install -y python3 git
```

macOS 上先装 [Homebrew](https://brew.sh/zh-cn/)，再执行 `brew install python git`。

**Q：第一行命令报错"command not found"？**
说明这台机器没有 `curl`。Ubuntu 上先装：

```bash
sudo apt update && sudo apt install -y curl
```

**Q：不想用那行命令，有别的办法吗？**
有。打开 <https://github.com/tob124/motor-control-tutorial>，
点绿色的 `Code` 按钮 → 选 `Download ZIP` → 解压 →
在解压出来的文件夹里打开终端，执行：

```bash
bash install.sh --in-place
```

**Q：课程页面打不开 / 显示不全？**
先确认终端窗口还开着、没有报错红字。然后按 `Ctrl` + `R` 刷新浏览器
（Windows/Linux）或 `Cmd` + `R`（macOS）。

**Q：我想重新开始，清空学习记录？**
删掉课程目录里的 `.state` 文件夹即可：

```bash
rm -rf ~/robocon-control-course/.state
```

⚠️ 这会清空所有答题记录和作品说明，删之前先把作品导出保存。

**Q：怎么更新到最新版课程？**

```bash
cd ~/robocon-control-course
git pull
```

没有 git 的话，重新下载 ZIP 覆盖即可（学习记录在 `.state/` 里，不会被覆盖）。

**Q：课程会联网吗？会上传我的东西吗？**
不会。课程是**完全本地**的服务，只监听你自己的电脑
（`127.0.0.1`），学习记录只存在你硬盘的 `.state/` 目录里。
安装时唯一的联网动作是从 GitHub 下载课程本身。

**Q：需要 Docker 吗？**
不需要。44 个网页实验台、课程内容、作品导出全都**不用 Docker**。
只有可选的"真实 Linux 终端实验"（编译 C 代码、CAN 工具）才需要。
想启用时执行：

```bash
bash ~/robocon-control-course/scripts/setup-docker.sh --mirror aliyun
```

它会先打印将要做的改动并征求你同意，不会偷偷改你的系统。

---

## 五、Windows 怎么办

课程依赖 Linux 环境，Windows 上推荐用 **WSL2**（微软官方的 Linux 子系统，
免费、不需要装双系统）：

1. 以**管理员身份**打开 PowerShell（开始菜单搜 PowerShell，右键"以管理员身份运行"）
2. 执行：`wsl --install -d Ubuntu`
3. 重启电脑
4. 重启后会弹出 Ubuntu 窗口，设置一个用户名和密码（**密码输入时看不见，这是正常的**）
5. 在这个 Ubuntu 窗口里，按上面《二、装》那一行命令粘贴执行

装好后，课程页面在 Windows 的浏览器里访问 `http://127.0.0.1:8770/` 就能打开。

**以后启动更省事的办法**：双击课程目录里的 `start-windows.bat`。
它会自动找 WSL 或 Windows 版 Python、生成课程内容、启动服务并打开浏览器，
你不用再敲任何命令。（终端实验仍建议在 WSL 里做，因为编译链是 Linux 的。）

> 为什么不用原生 Windows？课程里的很多工具（编译链、CAN 工具、shell 脚本）
> 是 Linux 生态的，这些也正是 ROBOCON 电控最常用的环境。
> 让你在 Linux 里学，本身就是在学将来要用的东西。

---

## 六、还是装不上怎么办

把终端里**从第一条到报错为止的全部内容**复制下来，
连同下面两条命令的输出，一起发给带你的人（或贴到 GitHub 的 issue 里）：

```bash
uname -a
python3 --version
```

安装脚本遇到问题时也会主动提示这两条——它不会让你自己猜。

---

*课程作者：Connor He 和 Astra*
