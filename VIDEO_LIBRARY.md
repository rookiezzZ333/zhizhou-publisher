# 独立视频库

文章与视频是同级资源，分别存于 articles/ 与 videos/。视频目录中不再需要 article.md。

## 推荐目录

~~~text
videos/video_日期_主题/
  video.yaml
  douyin.md
  bilibili.md
  cover.jpg
  media/video.mp4
~~~

video.yaml 示例：

~~~yaml
title: 我的作品
file: media/video.mp4
cover: cover.jpg
created_at: 1790000000
bilibili_tags: [动画, 视觉设计]
bilibili_category: 动画 / 综合
bilibili_copyright: unset
bilibili_source: ''
~~~

created_at 为 Unix 秒数。file、cover 使用目录内相对路径。封面可留空；新建草稿时 file 也可为空。
bilibili_copyright 支持 unset、original、repost。转载需填写来源。
分区、创作类型和来源属于投稿备忘，需要在 B 站页面手动确认。

平台文案分别保存在 douyin.md 和 bilibili.md，格式均为首行一级标题、后接简介。B 站标签独立保存在配置中，自动填入标签控件，不把标签混入简介。

## 界面流程

1. 左侧选择「视频作品」，导入 MP4。
2. 按需修改作品名称、选择封面。
3. 选择抖音或 B 站，编辑并保存各自文案。
4. 首次打开平台登录窗口，确认账号。
5. 自动填入会上传视频并填写文案；B 站还会填写已保存的标签。
6. 在平台确认上传转码、封面、分区、声明与发布设置，再手动发布。
7. 在工作台的发送记录确认已手动发布。

视频上限 500 MB，封面上限 12 MB。它们是工具限制，不是平台永久规格。
工作台封面用于本地预览与手动选用，不宣称已经自动设置到平台。
B 站本版不自动选择分区、不代选自制 / 转载或 AI 声明，也不点击最终投稿。

## 旧结构迁移

程序读取旧 article.md 中的 douyin_video，复制 MP4、封面与独立文案至 videos/，校验视频哈希后关联原抖音视频任务。原任务状态、时间、截图和人工确认均保留；已有素材快照不覆盖。

原始文章和 MP4 保留本机。迁移标记用于防止重复导入，删除视频后不会在下次启动时重新出现。删除视频使用 .state/trash/videos/ 回收目录，不影响平台作品。

## B 站适配与验证范围

使用独立 Edge profile 和 Playwright，支持主页面或内嵌 iframe 的上传入口。仅在找到唯一视频输入、唯一标题和简介控件后填写，回读验证文案；页面变化时停止并保留截图。

目前通过本地模拟创作页测试，未以真实 B 站账号完成上传转码验证。实际字段和限制以当前平台为准。

资料入口：
- [B 站创作中心投稿页](https://member.bilibili.com/platform/upload/video/frame)
- [B 站创作中心关于网页投稿字段的说明](https://www.bilibili.com/opus/127210875404901820)
- [B 站关于创作者内容标识的公告](https://www.bilibili.com/opus/840812291428450327)

以上旧公告用于了解字段用途，不作为当前页面结构或永久规则依据。
