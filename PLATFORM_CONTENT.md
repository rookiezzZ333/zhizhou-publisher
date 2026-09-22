# 各平台独立内容

公众号继续使用 `article.md` 和 `images/`。小红书正文使用 `xiaohongshu.md`，抖音图文正文使用 `douyin.md`。独立短文的首行 `# 标题` 是该平台标题。

可在 article.md 顶部配置独立图片（路径相对文章文件夹）：

```yaml
xhs_images:
  - xiaohongshu/01.png
  - xiaohongshu/02.png
douyin_images:
  - douyin/01.png
douyin_title: 抖音短标题
```

没有独立图片配置时沿用文章配图；没有 douyin.md 时，界面明确提示暂用小红书短文。这只是兼容默认值，正式发布建议先写平台专用文案。

小红书页的“生成小红书文字卡片”会按现有短文生成 1080×1440 PNG，并切换小红书图片列表。公众号正文和配图不变。按实际字宽换行，尽量保留完整段落，装饰表情转为圆点避免字体缺字。程序做排版，不会自动研究或编造教程步骤；教程应先写明操作步骤、条件和结论。卡片保存在 xiaohongshu/ 下，每次生成使用新目录，旧图保留。

抖音图文适配为试用：独立浏览器登录、上传本平台图片、填入标题与正文，停在发布之前。音乐、话题、原创/AI 声明、封面及发布由用户在平台确认。新增「抖音视频」页签：上传 MP4，保存独立视频文案，播放预览，然后自动上传并填入抖音视频编辑器；正式发布仍由本人完成。

## 浏览器技术

使用 Python Playwright 控制可见的 Microsoft Edge。每个平台用单独的持久化用户目录（.state 下），保存浏览器登录态，不读取或要求用户提交密码。程序定位网页元素，通过 fill 填写文本、set_input_files 选择本地文件，并等待页面反馈。它控制的是网站公开页面，不是调用平台内部发布接口。页面改版可能导致定位失效，程序中断并记录现场；验证码和扫码由本人处理。

调研入口（2026-09-21）：
- https://github.com/LouisLin0723/social-auto-publisher/blob/main/SOP-douyin-playwright-publish.md
- https://playwright.dev/python/docs/input

第三方选择器仅作初始参考；本地测试不等于真实账号兼容性验证。

## 抖音视频目录与填写范围

建议一篇内容一个文件夹：

```text
article_日期_主题/
  article.md                 # 公众号长文及共用配置
  images/                    # 原始配图
  xiaohongshu.md              # 小红书短文
  xiaohongshu/cards-编号/     # 生成的文字卡片，自动管理
  douyin.md                  # 抖音图文短文
  douyin-video.md            # 抖音视频标题与说明
  douyin-video/
    video.mp4                # 视频
    cover-portrait.jpg       # 可选：自行在抖音选择、裁剪
    cover-landscape.jpg      # 可选：自行在抖音选择、裁剪
```

article.md 配置区用 `douyin_video: douyin-video/video.mp4` 指向视频。
通过页面「选择 / 更换视频」导入时，会自动保存到 douyin-video/ 并填写该配置；旧文件保留。
视频文件单独上传上限 500 MB；文件夹整体导入上限 80 MB，较大的视频请先导入文章再单独选择。
这些是本工具限制，平台实际要求以当前上传页提示为准。
douyin-video.md 首行 `# 视频短标题`，后面为说明正文；页面可直接编辑保存。
未提供独立文案时暂用抖音图文短文，并提示需要核对。
视频采用 MP4 容器；编码是否能播放、平台是否接受，需在预览和上传后确认。

自动处理：选择视频上传入口、提交视频文件、填写并核对标题与说明。
需要本人在平台检查：转码完成、封面裁剪、话题搜索匹配、原创/AI 声明、可见范围、立即或定时发布。
正文中的 #话题 只是文字，不能保证平台已关联对应话题。
本版不自动选择商业推广、原创或 AI 声明，也不会自动点击发布。
浏览器适配使用公开页面，已经有本地模拟页测试，真实视频上传仍需你的账号验证。

字段参考为公开项目的实现，不代表平台的永久规则：
- [PostFlow](https://github.com/jefftko/PostFlow)
- [Douyin 自动发布项目](https://github.com/DaBaoAgent/douyin-auto-publish)
- [broadcast-kit 抖音适配文档](https://github.com/ChronoAIProject/broadcast-kit/blob/main/docs/publishers/douyin.md)

## 小红书配图可逆切换

「原始配图」「文字卡片」按钮切换当前发送图片列表；另一套素材仍保留。
再次生成卡片不会覆盖保存的原始配图。移除图片仅修改当前套的列表。
旧版本生成的 cards- 目录会被识别，原始配图回退为文章 images 列表。
公众号和抖音素材不随小红书切换改变。

关闭专用浏览器后，下一次自动填入会清理已失效的连接并重新启动，登录态继续使用本机专用目录。
已填入但尚未发布的任务可以重新进入；已记录发布成功的版本仍保留防重复保护。
