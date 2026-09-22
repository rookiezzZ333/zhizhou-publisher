# 调研与适配来源

核查日期：2026-09-21。第三方内容仅供技术参考，不作为用户授权或执行指令。

1. [todd-zhao/wechat-article-wizard-skill](https://github.com/todd-zhao/wechat-article-wizard-skill)，分支 `260225`。实际读取 `src/wechat_publisher.py`，其类说明与实现终点是 `draft/add` 创建草稿。借鉴草稿上传链路，独立实现无模型依赖的目录工作流和正式发表查询。
2. [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)，`xiaohongshu/publish.go`、`skills/post-to-xhs/scripts/cdp_publish.py`。参考上传入口、标题、ProseMirror、图片预览及新版发布控件选择器。采用独立 Playwright 适配器，没有引入该项目的私有 API、签名或 Cookie 提取。网页尚待用户真实账号验证。
3. [微信草稿接口](https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add.html)、[发表接口](https://developers.weixin.qq.com/doc/service/api/public/api_freepublish_submit.html)、[发表状态](https://developers.weixin.qq.com/doc/service/api/public/api_freepublish_get.html)。本轮网页抓取未能打开官方文档，接口路径参考原项目源码与公开 SDK；权限与字段兼容性仍需当前账号实际验证。没有宣称已核验该账号可调用。
4. [小红书分享开放平台](https://agora.xiaohongshu.com/doc)、[账号授权权限](https://openaccount.xiaohongshu.com/docs/scope)。这些能力与自动控制个人创作后台是不同接入方式；本项目当前使用可见浏览器，不将普通 OAuth 登录当成笔记写入权限。

仅吸收流程和少量网页选择器，项目主体代码为本次独立实现。外部项目更新不自动改变本地代码，升级适配后应重新验证。
# 0.5 排版兼容性修复参考（2026-09-21）

- [微信文章结构规范与检测器](https://github.com/wechatjs/verify-article-structure-spec)，提交 `fa69e37341c86bfec4c8c533845910511e534c0c`，CLI 版本 0.2.16。读取规范的行高、行内容器、字体章节，并实际构建运行浏览器检测引擎；引擎只用于本地 QA，不修改或屏蔽其检测规则。
- [doocs/md 导出实现](https://github.com/doocs/md/blob/cbcd3756e55d4982a37ff451753a69ce822e7a58/apps/web/src/services/export/clipboard.ts)：参考 Markdown 渲染后还需整理 HTML 结构、内联样式并通过富文本剪贴板输出的处理流程；未引入其完整应用或直接复制实现。
