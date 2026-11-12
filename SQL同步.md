在 stock-service 目录下执行:

# 1. 查看本地库与 NAS 库的连接状态和各表行数对比

python db_sync.py status

# 2. 平时 NAS 关机不用管；等哪天 NAS 开机后，把 Mac 最新数据一键备份到 NAS

python db_sync.py push-to-nas

# 3. 手动查看/启动/停止 Mac 本地 PostgreSQL 服务

./pg_local.sh status
