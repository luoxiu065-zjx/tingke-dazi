# 听课搭子

给在英国（以及其他英语国家）上课的中国留学生：英文课**实时转写**、**逐句中文翻译**、**AI 按主题总结**、**老师提问提醒**，下课自动存成 Markdown 笔记，可以直接放进 Obsidian。免费使用，源码公开。

- 官网与图文教程：<!-- 待补：网站地址 -->
- 原作者：[luoxiu065-zjx](https://github.com/luoxiu065-zjx) · 原始仓库：https://github.com/luoxiu065-zjx/tingke-dazi
- 许可证：[PolyForm Noncommercial 1.0.0](LICENSE) —— **个人学习、非商业用途免费使用、修改和分享**;**禁止任何商业用途**（包括售卖软件或安装包、付费代装、打包进付费课程/产品等）。转发或修改时必须保留 LICENSE 里的 `Required Notice` 版权声明。需要商业授权请联系作者。

## 能做什么

| 功能 | 说明 |
|---|---|
| 英文实时转写 | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 在你自己的电脑上跑，录音不上传。有 NVIDIA 显卡用 large-v3-turbo（实测约占 2.3GB 显存），没有就自动用 CPU + small.en |
| 逐句中文翻译 | 每句英文下面紧跟中文，术语保留英文括注(DeepSeek，用你自己的 API Key) |
| AI 按主题总结 | 约每 40 秒更新，老师换话题就开新主题 |
| 提问提醒 | 老师向全班提问时，给出他在问什么、中文要点、一句英文思路。**默认关闭**，是帮你听懂问题的学习辅助 |
| 按课程存笔记 | 开始前选课，下课保存：课后总结 + 课上提问与参考回答 + 全程中英对照 |
| 费用看得见 | 界面显示 DeepSeek 余额，每节课花费写进笔记。作者实测一小时的课约 ¥0.3–0.5 |

课后想对笔记接着提问、让 AI 把解释写回笔记：在 Obsidian 里装 [Claudian](https://github.com/YishenTu/claudian) 插件，可以和本项目用同一个 DeepSeek Key，见教程第 5 步。

## 安装(Windows 10/11)

1. 下载并解压到英文路径（如 `D:\tingke`)。
2. 双击 **`安装.bat`**：自动下载一个只放在本文件夹里的 Python 3.12，装好依赖；检测到 NVIDIA 显卡会额外装显卡加速库（约 1GB)。
3. 在弹出的记事本(`.env`)里填 `DEEPSEEK_API_KEY=你的Key`，保存。Key 在 [platform.deepseek.com](https://platform.deepseek.com/api_keys) 申请。
4. 双击 **`启动.bat`**，浏览器会打开 `http://127.0.0.1:5000`。第一次启动会下载语音模型（约 1.6GB)。

需要约 5GB 硬盘空间。在中国大陆下载慢，见 `.env` 里的 `HF_ENDPOINT` 说明。

## 配置(`.env`)

| 项 | 作用 |
|---|---|
| `COURSES` | 课程列表，逗号分隔，第一个词是课号（文件夹名） |
| `OBSIDIAN_VAULT` / `RECORD_SUBDIR` | 笔记存进 Obsidian 库的哪里；不填就存到程序旁边的 `课堂记录/` |
| `AUTO_QA` | 提问提醒默认开关(0/1)，录制时界面上也能开关 |
| `WHISPER_DEVICE` | `auto`（默认）/ `cuda` / `cpu` |

`术语/<课号>.txt` 里写这门课的英文专业词，能提高识别准确率，有示例。

## 数据去哪了

- 录音：只在你的电脑上转写，不上传、不保存音频。
- 转写出的**英文文字**：发给 DeepSeek 做翻译和总结（你自己的 Key)。
- 笔记：存在你指定的文件夹。
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
```

`启动.bat` 会优先用 `python\`（安装包），其次用 `venv\`。`app.py --no-audio` 只起网页、不加载模型，用于界面调试。

## 致谢

[faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [OpenAI Whisper](https://github.com/openai/whisper) · [SoundCard](https://github.com/bastibe/SoundCard) · [DeepSeek](https://www.deepseek.com) · [Claudian](https://github.com/YishenTu/claudian)
