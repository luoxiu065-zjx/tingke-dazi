# 听课搭子

给在英国（以及其他英语国家）上课的中国留学生：英文课**实时转写**、**逐句中文翻译**、**AI 按主题总结**、**老师提问提醒**，再加**翻译**、**AI 问答**、**课件参考**、**了解我**四个可拖动的模块，下课自动存成 Markdown 笔记，可以直接放进 Obsidian。免费使用，源码公开。

- 官网与图文教程：http://43.165.7.249:8092/
- 原作者：[luoxiu065-zjx](https://github.com/luoxiu065-zjx) · 原始仓库：https://github.com/luoxiu065-zjx/tingke-dazi
- 许可证：[PolyForm Noncommercial 1.0.0](LICENSE) —— **个人学习、非商业用途免费使用、修改和分享**；**禁止任何商业用途**（包括售卖软件或安装包、付费代装、打包进付费课程/产品等）。转发或修改时必须保留 LICENSE 里的 `Required Notice` 版权声明。需要商业授权请联系作者。

## 第一次用,照着做(约 15 分钟,不用改任何文件)

只要一台 Windows 10 / 11 电脑。有 NVIDIA 显卡最好,没有也能用。Mac、iPad、手机请用网页版(见 `phone/`)。

| 步骤 | 你要做的 | 会看到什么 |
|---|---|---|
| 1. 下载 | 官网点「下载 Windows 版」,把压缩包**解压到 D 盘这类英文路径**(比如 `D:\tingke`),别直接在压缩包里双击 | 一个文件夹,里面有 `安装.bat`、`启动.bat` |
| 2. 安装 | 双击 `安装.bat`,等它跑完(几分钟,网速慢会久一点) | 黑窗口最后写「安装完成」。Windows 弹「已保护你的电脑」时点「更多信息」→「仍要运行」 |
| 3. 启动 | 双击 `启动.bat` | 浏览器自动打开听课搭子。第一次会下载语音模型(约 1.6GB),等进度走完 |
| 4. 照页面上的「开始前三步」做 | ① 点「去填」,贴 DeepSeek key(不会拿就看 [16 秒录屏](http://43.165.7.249:8092/media/guide/deepseek-key.mp4))② 点「改课程」,一行写一门课 ③ 右上角选声音来源 | 三步都打勾后这张卡会自己消失,点「开始录制」就能上课 |

全过程有一段 2 分半的跟着点视频:[电脑版教程](http://43.165.7.249:8092/tutorial.html)。

### 遇到这个怎么办

| 现象 | 怎么办 |
|---|---|
| 双击 `安装.bat` 被 Windows 拦住 | 点「更多信息」→「仍要运行」。如果开了「智能应用控制」完全打不开,改用网页版 |
| 安装到一半报网络错误 | 再双击一次 `安装.bat`,已下载的部分不会重下 |
| 设置里「测试连接」说余额不足 | DeepSeek 要先充值,几块钱够用很久(一小时课约 3–5 毛) |
| 一直没有字幕 | 看右上角声音来源选对没有:教室里选「麦克风」,网课选「电脑播放的声音」 |
| 字幕出来了但没有中文 | 第 4 步的 DeepSeek key 没填或没通过测试 |
| 想把笔记放进 Obsidian | 双击 `配置Obsidian.bat`,粘一次 DeepSeek key,自动装好并连上 |

## 能做什么

| 功能 | 说明 |
|---|---|
| 英文实时转写 | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 在你自己的电脑上跑，录音不上传。有 NVIDIA 显卡用 large-v3-turbo（实测约占 2.3GB 显存），没有就自动用 CPU + small.en |
| 逐句中文翻译 | 每句英文下面紧跟中文，术语保留英文括注。翻译排队时自动几句合并，不会越攒越多 |
| AI 按主题总结 | 约每 40 秒更新，老师换话题就开新主题 |
| 老师提问提醒 | 老师向全班提问时，给出他在问什么、中文要点、一句英文思路。**默认关闭**，是帮你听懂问题的学习辅助 |
| 四格画布 | 录制页是固定 2×2 的画布，模块是可以拖的积木：拖进格子、选占几格、收回右侧小圆圈。摆法记在浏览器里 |
| 翻译模块 | 贴一段英文翻，或者在转写里选中一段点「译」 |
| AI 问答模块 | 有啥不懂直接问。它知道这节课的转写、AI 笔记和这门课以前问过的所有问题；每条回答可以「写进笔记」 |
| 添加课件 | pdf / pptx / md / txt 随时加，AI 总结和提问会参考最相关的几页；课件里的术语自动补进术语表 |
| 了解我 | 可选：把你的自述、老师画像、学习状态、最近两节课等打成一段背景，让回答更贴合你。**默认关闭** |
| 移交手机 | 电脑要没电或要走了：点一下，这节课到目前为止的转写、总结、提问原样移到手机 / 平板网页版（见 `phone/`），手机上点「继续」接着录，最后由手机那边存成一篇完整笔记。需要在设置里填手机版专属链接 |
| 断电不丢 | 录制中每 2 秒落盘；程序意外退出再开，自动把那节课存好。没插电、电量低于 8% 自动结束保存 |
| 设置页 | 左下「⚙ 设置」：DeepSeek / 通义千问 / Moonshot / OpenAI / 本机 Ollama / 自定义地址，贴 key、测试、保存即生效 |
| 费用看得见 | DeepSeek 显示余额和本节课花费；其他提供商显示本节课 token 用量。作者实测一小时的课约 ¥0.3–0.5 |

课后想对笔记接着提问、让 AI 把解释写回笔记：在 Obsidian 里装 [Claudian](https://github.com/YishenTu/claudian) 插件，可以和本项目用同一个 DeepSeek Key，见教程第 5 步。

## 安装（Windows 10/11）

1. 下载并解压到英文路径（如 `D:\tingke`）。
2. 双击 **`安装.bat`**：自动下载一个只放在本文件夹里的 Python 3.12，装好依赖；检测到 NVIDIA 显卡会额外装显卡加速库（约 1GB）。
3. 双击 **`启动.bat`**，浏览器会打开 `http://127.0.0.1:5000`。第一次启动会下载语音模型（约 1.6GB）。
4. 页面上会出现「开始前三步」卡片:点「去填」贴 DeepSeek 的 API key(在 [platform.deepseek.com](https://platform.deepseek.com/api_keys) 申请),点「改课程」写上你的课,右上角选声音来源。

需要约 5GB 硬盘空间。在中国大陆下载慢，见 `.env` 里的 `HF_ENDPOINT` 说明。

## 配置（`.env`，也可以在界面里改）

| 项 | 作用 |
|---|---|
| `COURSES` | 课程列表(页面上「✎ 改课程」就能改,不用动这里),逗号分隔,第一个词是课号(文件夹名) |
| `OBSIDIAN_VAULT` / `RECORD_SUBDIR` / `COURSE_SUBDIR` | 笔记存进 Obsidian 库的哪里；不填就存到程序旁边的 `课堂记录/` |
| `AUTO_QA` | 提问提醒默认开关(0/1)，录制时界面上也能开关 |
| `CONTEXT_MODE` | 「了解我」默认开关(0/1)。开之前先填 `profile/我.md`（有 `我.example.md` 模板） |
| `WHISPER_DEVICE` | `auto`（默认）/ `cuda` / `cpu` |

`术语/<课号>.txt` 里写这门课的英文专业词，能提高识别准确率，有示例。`profile/` 是「了解我」的个人档案，只在本机、只进模型请求。

## 数据去哪了

- 录音：只在你的电脑上转写，不上传、不保存音频。
- 转写出的**英文文字**：发给你自己配的模型提供商做翻译和总结（你自己的 Key）。
- 笔记、问答记忆、课件文本、个人档案：都在程序目录或你指定的文件夹里。
- 每识别一句会刷新程序目录下的 `live.md`（当前这节课的实时内容），方便其他 AI 工具读取；不需要可以忽略。

## 请注意

- 各学校、各门课对课堂录音的规定不同，使用前请查看相关政策，需要时征得老师同意；录音内容别分享给别人。
- 提问提醒只帮你理解问题，不替你作答；正式考试、口语评估时请关闭。
- 机器转写和翻译会出错，重要内容以课件和老师原话为准。

## 开发

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt   # 有 N 卡再装 requirements-gpu.txt
copy .env.example .env
venv\Scripts\python app.py
venv\Scripts\python tests\test_session.py     # 其余 tests\test_*.py 同理,都不联网
```

`启动.bat` 会优先用 `python\`（安装包），其次用 `venv\`。`app.py --no-audio` 只起网页、不加载模型；网址加 `?preview=1` 是假数据预览，用于改界面。

## 课后答疑：一键配好 Obsidian

双击 **`配置Obsidian.bat`**，按提示粘一次 DeepSeek key，它会：建好笔记库 → 装好 Obsidian / Node / Git / Claude Code（缺什么装什么）→ 把 Claudian 插件装进库并指向 DeepSeek、模型也选好 → 把听课搭子的记录目录指到这个库 → 打开 Obsidian。

打开后 Obsidian 会问一次「是否信任这个库的插件」，点**信任仓库作者并启用插件**，就能在右侧栏直接问问题了，不用登录、不用选模型。

已经在用 Obsidian？把你的库路径作为参数传给它：`配置Obsidian.bat D:\我的库`。如果那个库的 Claudian 已经配过别的（比如 Claude 订阅），脚本会原样保留、不覆盖。

## 手机 / 平板网页版（测试中）

没带电脑、只有手机或平板时用的版本,在 [`phone/`](phone/README.md):手机浏览器打开网页、录老师的声音,识别(Groq)和翻译总结(DeepSeek)在**你自己的服务器**上做,下课存成同样格式的笔记。设计为一个人用,想用请自己部署一份(约 15 分钟,README 里有步骤)。作者托管的公开版还在小范围测试,想试的在 issue 里留言。

## 致谢与灵感来源

产品形态受 Coursedude（coursedude.ai）启发：先选课、再录制、下课自动整理。本项目代码全部自写，界面自行设计，与 Coursedude 无关联，也不是其替代品；非商业，个人学习用。

[faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [OpenAI Whisper](https://github.com/openai/whisper) · [SoundCard](https://github.com/bastibe/SoundCard) · [PyMuPDF](https://pymupdf.readthedocs.io/) · [python-pptx](https://python-pptx.readthedocs.io/) · [DeepSeek](https://www.deepseek.com) · [Claudian](https://github.com/YishenTu/claudian) · 教材和日程由 Claude Code 生成
