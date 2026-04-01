# 部署说明

## 本地运行

在项目目录执行：

```powershell
python .\scrape_jwc.py
```

默认访问地址：

```text
http://127.0.0.1:8000/
```

## Render 部署

这个项目需要部署为 `Web Service`，不是 `Static Site`。

### 方式一：使用仓库里的 `render.yaml`

1. 把当前项目上传到 Git 仓库。
2. 打开 Render，选择 `New` -> `Blueprint` 或 `Web Service`。
3. 连接你的 Git 仓库。
4. Render 读取到仓库根目录的 `render.yaml` 后，会按里面的配置创建服务。
5. 等待部署完成后，Render 会分配一个公开网址，格式通常类似：

```text
https://xxxx.onrender.com
```

### 方式二：手动在 Render 面板填写

如果你不想依赖 `render.yaml`，也可以在 Render 后台手动填写：

- Service Type: `Web Service`
- Runtime: `Python`
- Build Command: `pip install -r requirements.txt`
- Start Command: `python scrape_jwc.py`

### 说明

- 页面和接口都由 `scrape_jwc.py` 提供，所以必须部署成带后端的 Web Service。
- 程序会自动读取 Render 提供的 `PORT` 环境变量，不需要手动指定端口。
- 当前项目依赖 Python 标准库，因此 `requirements.txt` 仅作为 Render 构建入口保留。
