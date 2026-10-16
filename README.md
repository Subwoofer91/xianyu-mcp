# 中国电商登录态浏览器 MCP

一个仅供本机使用、只读、依赖 Chrome 登录态的中国电商 MCP。支持：

- 闲鱼（Xianyu / Goofish）
- 淘宝与天猫
- 京东
- 拼多多

它与免登录的 [`cn-ecommerce-mcp`](https://github.com/Subwoofer91/cn-ecommerce-mcp)
相互独立：前者读取当前登录账号实际看到的动态商品卡片，本项目因此更适合核对实时 SKU、
个性化价格和一般优惠；后者不依赖浏览器，适合无状态的公开索引搜索。

## 工具

- `browser_status`：只返回各平台 Cookie 的有无与数量，不返回 Cookie 内容。
- `search_products`：统一搜索闲鱼、淘宝、京东、拼多多，返回商品介绍、卡片价格、一般优惠、主图和直链。
- `inspect_product`：打开商品详情页，返回页面介绍、价格、优惠和图片。
- `build_search_links`：生成各平台官方搜索链接。
- `parse_product_link`：验证并规范化四个平台的官方商品链接。
- `search_xianyu` / `build_xianyu_search_link`：兼容早期闲鱼版客户端。

全部工具都是只读的。代码没有聊天、收藏、加入购物车、下单或支付能力。

## 工作方式

项目使用 Playwright 驱动本机已安装的 Google Chrome，并使用仓库内的
`.browser-profile` 作为专用持久化配置。普通 Chrome 的现有登录不会被自动复制；第一次使用时，
你需要在这个专用 Chrome 窗口中登录一次。之后 MCP 默认复用这个可见的 Chrome，便于网站
正常识别浏览器并让你处理偶发验证；关闭 MCP 进程时该窗口会一并关闭。

这样做可以避免读取或破坏日常 Chrome 配置，也避免把 Cookie 复制到云端。`.browser-profile`
已被 `.gitignore` 排除，绝不能提交、打包或分享。

## Windows 本地安装

本项目属于普通 Python 工具，建议使用 `playground` 环境：

```powershell
conda run -n playground python -m pip install -r requirements.txt
```

### 1. 创建登录态

先确保没有正在运行的本 MCP 进程，然后执行：

```powershell
conda run -n playground python login_browser.py
```

脚本会打开四个标签页。请在浏览器中手动登录并完成网站要求的验证，再回终端按 Enter。
密码、短信验证码和扫码过程都不会进入 MCP。

只更新部分平台也可以：

```powershell
conda run -n playground python login_browser.py xianyu taobao
```

### 2. 注册到 Codex

使用绝对路径注册 stdio MCP：

```powershell
codex mcp add cn-ecommerce-browser -- `
  C:\Users\Subwoofer\miniforge3\envs\playground\python.exe `
  C:\absolute\path\to\xianyu-mcp\server.py
```

检查：

```powershell
codex mcp list
```

### 3. 运行测试或独立实测

```powershell
conda run -n playground python -m unittest discover -s tests -v
conda run -n playground python server.py --live-test "iPhone 13 mini"
```

## 可选配置

- `ECOMMERCE_BROWSER_PROFILE`：专用 Chrome profile 目录。
- `ECOMMERCE_BROWSER_CHANNEL`：Playwright 浏览器频道，默认 `chrome`。
- `ECOMMERCE_BROWSER_HEADLESS=1`：改为无头运行；默认显示浏览器。部分平台会拒绝无头浏览器，
  因此只建议在确认当前站点可用时开启。
- `ECOMMERCE_CDP_URL`：高级用法，连接到你自行启动并保护好的 Chrome DevTools 地址。

不要把 CDP 端口暴露到局域网或公网；它等同于远程控制浏览器和登录账号。

## 价格与优惠边界

返回值是抓取时该账号、地区、商品卡片或详情页上可见的价格，不是保证成交价。淘宝、京东和
拼多多可能把最低规格、多人团、以旧换新、会员价或满足条件后的券后价显示为主价格；闲鱼商品
也可能已经售出或被卖家修改。因此工具会保留：

- `price_kind`：价格来自搜索卡片还是详情页；
- `observed_at`：抓取时间；
- `item_url`：官方商品直链；
- `verification_required`：下单前仍需核对选中 SKU、优惠资格、运费和结算页。

如果登录失效或触发风控，工具会返回 `login_required` 或 `challenge_required`，不会尝试绕过。

## License

[MIT](LICENSE)
