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

项目先以普通方式启动本机 Google Chrome，只增加绑定到 `127.0.0.1` 的 DevTools 端口；
随后 Playwright 通过 CDP 连接这个已经运行的浏览器。它不会使用
`--enable-automation`、`--remote-debugging-pipe` 或 `--no-sandbox`，从而减少京东等平台
把浏览器判定为自动化测试环境的概率。

这里的 Playwright 只作为 CDP/DOM 客户端使用：不使用 Codex Computer Use，不读取截图做视觉
定位，也不模拟鼠标或键盘点击。搜索结果直接从页面 DOM、商品卡片运行时数据和官方链接中读取；
账号登录与验证码始终由用户在普通 Chrome 中手动完成。

Chrome 使用仓库内的 `.browser-profile` 作为专用持久化配置。日常 Chrome 的现有登录不会被
自动复制；第一次使用时，需要在这个专用普通 Chrome 中登录一次。MCP 退出后 Chrome 可以继续
运行，后续工具调用会重新连接它。

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

脚本会用普通 Chrome 打开四个标签页后立即退出。请在浏览器中手动登录并完成网站要求的验证，
然后保持该 Chrome 运行。密码、短信验证码和扫码过程都不会进入 MCP。

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
- `ECOMMERCE_CHROME_EXECUTABLE`：本机 `chrome.exe` 的绝对路径；通常会自动发现。
- `ECOMMERCE_CDP_PORT`：本地 DevTools 端口，默认 `9223`。
- `ECOMMERCE_CDP_URL`：高级用法，连接到你自行启动的本机 Chrome；只接受 loopback HTTP 地址。

不要把 CDP 端口暴露到局域网或公网；它等同于远程控制浏览器和登录账号。本项目固定使用
`--remote-debugging-address=127.0.0.1`，并拒绝配置非 loopback 的 CDP 地址。

## 价格与优惠边界

返回值是抓取时该账号、地区、商品卡片或详情页上可见的价格，不是保证成交价。淘宝、京东和
拼多多可能把最低规格、多人团、以旧换新、会员价或满足条件后的券后价显示为主价格；闲鱼商品
也可能已经售出或被卖家修改。因此工具会保留：

- `price_kind`：价格来自搜索卡片还是详情页；
- `observed_at`：抓取时间；
- `item_url`：官方商品直链；
- `verification_required`：下单前仍需核对选中 SKU、优惠资格、运费和结算页。

如果登录失效或触发风控，工具会返回 `login_required` 或 `challenge_required`，不会尝试绕过。
若详情页主动隐藏价格（例如拼多多显示 `1??9.??` 并要求前往 App），工具会返回
`displayed_price_cny: null` 和 `price_kind: masked_or_unavailable`，而不会把掩码误报为 1 元；
此时可参考同一次搜索返回的商品卡片价格，并在下单前再次核对。

## License

[MIT](LICENSE)
