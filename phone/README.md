# 听课搭子 · 手机 / 平板网页版(自部署,多人)

没带电脑、只有手机或平板时用的版本:手机浏览器打开网页 → 录老师的声音 → 服务器上做识别(Groq whisper-large-v3-turbo)和翻译 / 总结 / 提问(DeepSeek)→ 实时推回手机 → 下课存成 Markdown 笔记,同步到各人自己的 Obsidian。

iPhone(Safari)、安卓(Chrome)、iPad 都能用,不用装 App。**部署一份可以给一群人用**:每人在页面上点「开始使用」拿到专属链接,各填各的 Groq / DeepSeek key(钱各付各的),笔记各自同步,互不可见。

> 电脑版见[仓库首页](../README.md)。

## 每个用户看到的流程

1. 打开 `https://你的域名/live/` → 开始使用 → 得到专属链接(收藏或加到主屏幕)。
2. 向导三步:填 Groq / DeepSeek key(可先跳过,用试用额度)→ 选笔记同步方式 → 发一份测试笔记确认。
3. 选课 → 开始录制。三个页签:转写 / 总结 / 提问。结束并保存。

同步方式三选一:**托管同步文件夹**(服务器给他开一个私人 WebDAV `/udav/<uid>/`,Obsidian 装 Remotely Save 指过来,默认)、**他自己的 WebDAV**(坚果云 / 自建,下课后推过去)、**不同步只下载**(服务器留 7 天)。

## 你需要

- 一台能 24 小时开着、有公网地址的 Linux 机器(最便宜的云主机就够,音频切片很小,服务器不跑模型)
- Python 3.10+、nginx(带 `dav_ext` 模块:`apt install nginx libnginx-mod-http-dav-ext`)
- 一个域名或能签 HTTPS 的地址:**手机浏览器只在 HTTPS 下允许用麦克风**(`sslip.io` 这类免费域名 + Let's Encrypt 就行)
- (可选)一个 [Groq](https://console.groq.com) key 和一个 [DeepSeek](https://platform.deepseek.com) key 作为公共 key,给你自己用 + 新用户试用

## 部署(约 20 分钟)

```bash
git clone https://github.com/luoxiu065-zjx/tingke-dazi.git
cd tingke-dazi/phone
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env        # 填 ACCESS_TOKEN、PUBLIC_BASE_URL;公共 key 可选
sudo bash deploy/setup-dav.sh            # 建 /srv/udav 和 /srv/udav-auth,设好 nginx 和后端共用的权限
python server.py                          # 先手动跑一次,浏览器开 http://127.0.0.1:8095/live/health 返回 ok 就对了
```

常驻运行(systemd 用户服务,改掉路径):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/tingke-live.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now tingke-live
loginctl enable-linger $USER              # 不登录也保持运行
```

HTTPS 反代:把 `deploy/nginx-live.conf` 里的两个 location(`/live/` 反代后端,`/udav/` 托管 WebDAV)放进你 443 的 server 块,证书用 certbot。然后手机打开 `https://你的域名/live/`。

你自己的链接是 `https://你的域名/live/?k=你的ACCESS_TOKEN`(owner 账号:用 .env 里的公共 key、不限试用、记录写进 VAULT_DIR)。

## 费用与额度

- 服务器不跑模型,只转发几秒钟的音频片段,最小的云主机够几十个人同时用。
- 识别走 Groq,每个用户用自己的 key(免费档每天 8 小时音频,一个人够用);翻译总结走 DeepSeek,一小时课约 ¥0.3–0.5,各付各的。
- 试用:没填 key 的新用户可用你 .env 里的公共 key 识别 `TRIAL_SECONDS` 秒,全站每天合计 `TRIAL_DAILY_CAP` 秒。不想开放试用就把 `GROQ_API_KEY` 留空。

## 和电脑版的区别

| | 电脑版 | 网页版 |
|---|---|---|
| 识别在哪跑 | 你的电脑(录音不出门) | 服务器 → Groq(音频会上传到 Groq) |
| 模块 | 四格仪表盘,可拖 | 固定三个页签 |
| 课件 / 了解我 / AI 问答 | 有 | 还没有 |
| 多人 | 单机 | 每人一把钥匙、各自的 key 和笔记 |

## 请注意

- 各学校对课堂录音规定不同,用之前看一下政策,必要时征得老师同意。
- 用户的 key 以明文存在服务器的 `data/users.json`(权限 600),托管同步的笔记存在 `DAV_ROOT`。部署给别人用,请告诉他们这一点。
- 许可证与仓库相同:PolyForm Noncommercial,个人学习免费,禁止商用。
