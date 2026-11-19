在 stock-service 目录下执行:

# 在执行下面的内容前，需要先激活虚拟环境（虚拟环境只会对当前终端有影响，不会干扰其他运行的终端。关闭终端即可自动退出虚拟环境）

source .venv/bin/activate

# 1. 查看本地库与 NAS 库的连接状态和各表行数对比

python db_sync.py status

# 2. 平时 NAS 关机不用管；等哪天 NAS 开机后，把 Mac 最新数据一键备份到 NAS

python db_sync.py push-to-nas

# 3. 手动查看/启动/停止 Mac 本地 PostgreSQL 服务

./pg_local.sh status
