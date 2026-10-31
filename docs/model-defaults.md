# 安装包默认模型配置

`services/api/model-defaults.local.json` 保存发布时使用的默认对话和 Embedding 配置，由 Tauri 打包到本地服务资源目录。该文件包含共享 API Key，已加入 `.gitignore`；在其他构建机器上需要单独提供，不能提交到源码仓库。

新账号首次读取模型配置时，会将默认配置保存到自己的本地设置中并直接使用。已有设置的账号不会被覆盖，也不会将个人地址与默认密钥混用。之后修改模型配置只影响当前账号；保存界面返回的掩码不会清空已有密钥。更新安装包默认值不会覆盖账号已保存的配置。

默认配置字段：`provider`、`base_url`、`api_key`、`model`、`embedding_base_url`、`embedding_api_key`、`embedding_model`、`enabled`。发布配置应使用 `enabled: true`。

此方式会将共享凭据分发给安装包接收者；界面脱敏不能阻止从安装包提取密钥。公开分发时应改为受鉴权和额度控制的服务端网关，供应商密钥仅保存在服务端。

验证：`python3 -m unittest evals.test_model_defaults`；桌面构建：`pnpm tauri build --bundles app`。
