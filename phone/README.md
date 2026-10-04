# 听课搭子 · 手机 / 平板网页版(自部署)

没带电脑、只有手机或平板时用的版本:手机浏览器打开网页 → 录老师的声音 → 服务器上做识别(Groq whisper-large-v3-turbo)和翻译 / 总结 / 提问(DeepSeek)→ 实时推回手机 → 下课存成 Markdown 笔记,放进你指定的目录(建议是你的 Obsidian 同步目录,这样手机和电脑都能看到)。

iPhone(Safari)、安卓(Chrome)、iPad 都能用,不用装 App。**设计为一个人用**:一把钥匙、同一时刻只录一节课。想给同学用,请让他们各自部署一份。

> 这是测试版,作者在自己的课上用着。电脑版见[仓库首页](../README.md)。

## 你需要

- 一台能 24 小时开着、有公网地址的 Linux 机器(最便宜的云主机就够,音频切片很小)
- Python 3.10+
- 一个 [Groq](https://console.groq.com) key(语音识别,有免费额度)和一个 [DeepSeek](https://platform.deepseek.com) key(翻译总结,一小时课约 ¥0.3–0.5)
- 一个域名或能签 HTTPS 的地址:**手机浏览器只在 HTTPS 下允许用麦克风**(`sslip.io` 这类免费域名 + Let's Encrypt 就行)

## 部署(约 15 分钟)

```bash
git clone https://github.com/luoxiu065-zjx/tingke-dazi.git
cd tingke-dazi/phone
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env        # 填两个 key、起一串 ACCESS_TOKEN、改 COURSES
python server.py                          # 先手动跑一次,不报错、浏览器开 http://127.0.0.1:8095/live/health 返回 ok 就对了
```

常驻运行(systemd 用户服务,改掉路径):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/tingke-live.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now tingke-live
loginctl enable-linger $USER              # 不登录也保持运行
```

HTTPS 反代(nginx 片段在 `deploy/nginx-live.conf`,放进你的 server 块;证书用 certbot):

```nginx
location /live/ { proxy_pass http://127.0.0.1:8095/live/; proxy_buffering off; proxy_read_timeout 1h; client_max_body_size 20m; }
```

然后手机打开 `https://你的域名/live/?k=你的ACCESS_TOKEN`,第一次会要麦克风权限。

## 用法

1. 选课 → 开始录制。录音时别锁屏(页面会申请常亮)。
2. 顶上三个页签:转写(英文 + 中文)/ 总结(约每 40 秒按主题更新)/ 提问(老师向全班提问时弹卡)。
3. 结束并保存 → 记录出现在 `RECORDS_DIR/课堂记录/<课号>/`。页面关了或断网,再打开会自动接上正在录的那节课;服务器重启也会把没结束的课自动存好。

## 和电脑版的区别

| | 电脑版 | 网页版 |
|---|---|---|
| 识别在哪跑 | 你的电脑(录音不出门) | 你的服务器 → Groq(音频会上传到 Groq) |
| 模块 | 四格仪表盘,可拖 | 固定三个页签 |
| 课件 / 了解我 / AI 问答 | 有 | 还没有 |
| 费用 | DeepSeek 用量 | 服务器 + DeepSeek 用量(Groq 免费额度一般够一个人用) |

## 请注意

- 各学校对课堂录音规定不同,用之前看一下政策,必要时征得老师同意。
- 音频会经过你的服务器和 Groq,别把 `ACCESS_TOKEN` 链接发给别人。
- 许可证与仓库相同:PolyForm Noncommercial,个人学习免费,禁止商用。
