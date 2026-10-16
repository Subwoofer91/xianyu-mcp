# 闲鱼免登录搜索 MCP

一个独立、只读、无需登录的闲鱼（Xianyu / Goofish）MCP 服务。它与
[`cn-ecommerce-mcp`](https://github.com/Subwoofer91/cn-ecommerce-mcp) 是两个不同的
项目、进程和 GitHub 仓库，可以同时部署。

## 能做什么

- `search_xianyu`：搜索公开网页索引中的闲鱼商品信息。
- `build_xianyu_search_link`：生成闲鱼网页版官方搜索链接。
- `parse_xianyu_item_link`：识别当前和旧版闲鱼商品链接并提取商品 ID。

搜索结果会尽量包含：

- 公开索引价格及原价/购入价；
- 全新、99 新、自用、二手等成色标签；
- 包邮、自提、验货宝、可议价等交易标签；
- 可确认的闲鱼商品直链；
- 公开来源链接、索引日期和新鲜度；
- 闲鱼官方搜索入口。

## 重要边界

闲鱼网页版商品列表通过动态接口加载，并可能触发登录、验证码或风控。本项目不会绕过
这些机制，也不会读取 Cookie、登录账号、联系卖家、拍下或付款。

因此，返回的是公开搜索引擎已经收录的信息，不是实时库存或成交价。它可能过期，商品
也可能已经售出。购买前必须在闲鱼页面确认：

- 当前价格、运费和是否仍在售；
- 准确型号、成色、维修史和配件；
- 卖家信用、验货方式和交易风险。

如果公开索引没有可用商品，工具仍会返回可直接打开的闲鱼官方搜索链接，不会编造结果。

## 本地运行

~~~bash
python -m pip install -r requirements.txt
python server.py
~~~

运行测试：

~~~bash
python -m unittest discover -s tests -v
~~~

执行真实免登录搜索：

~~~bash
python server.py --live-test "静音游戏鼠标"
~~~

本地注册到 Codex：

~~~bash
codex mcp add xianyu -- python /absolute/path/to/xianyu-mcp/server.py
~~~

## VPS 部署（Docker + Caddy）

远程服务使用 Streamable HTTP，端点为 `/mcp`。默认必须配置 Bearer Token；只有明确
设置 `MCP_ALLOW_ANONYMOUS=1` 才允许匿名访问。

前置条件：VPS 已安装 Git、Docker Engine、Docker Compose 插件和 Caddy，并有一个
已解析到该 VPS 的域名。

### 1. 启动独立容器

~~~bash
git clone https://github.com/Subwoofer91/xianyu-mcp.git
cd xianyu-mcp

TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf 'MCP_ACCESS_TOKEN=%s\nMCP_HOST=0.0.0.0\nMCP_PORT=8001\nMCP_LOG_LEVEL=info\n' "$TOKEN" > .env

docker compose up -d --build
curl http://127.0.0.1:8001/healthz
~~~

本项目使用 `127.0.0.1:8001`，不会与默认使用 8000 端口的 `cn-ecommerce-mcp`
冲突，也不会将 MCP 端口直接暴露到公网。

### 2. 配置 HTTPS

把 `deploy/Caddyfile.example` 中的 `xianyu-mcp.example.com` 替换成你的域名，将配置
加入 Caddy 后执行：

~~~bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
~~~

防火墙只需要开放 80/443。最终地址类似：

~~~text
https://xianyu-mcp.example.com/mcp
~~~

### 3. 注册远程 MCP

~~~bash
export XIANYU_MCP_TOKEN='替换为 .env 中的 MCP_ACCESS_TOKEN'
codex mcp add xianyu-remote \
  --url https://xianyu-mcp.example.com/mcp \
  --bearer-token-env-var XIANYU_MCP_TOKEN

codex mcp list
~~~

## License

[MIT](LICENSE)
