# pyHex - Django HEX解析器项目

## 项目结构

```
pyHex/
├── config/                      # Django项目配置
│   ├── settings.py              # 项目设置
│   ├── urls.py                  # 主URL配置
│   ├── wsgi.py                  # WSGI配置
│   └── asgi.py                  # ASGI配置
├── hexparser/                   # HEX解析器应用
│   ├── admin.py                 # Django后台管理配置
│   ├── models.py                # 数据模型定义
│   ├── views.py                 # 视图函数
│   ├── urls.py                  # 应用URL配置
│   ├── migrations/              # 数据库迁移文件
│   └── utils/                   # 工具类
│       ├── hex_client.py        # HEX文件解析客户端
│       ├── a2l_client.py        # A2L文件解析客户端
│       ├── a2l_importer.py      # A2L数据导入工具
│       └── excel_to_cfg_converter.py  # Excel转CFG工具
├── manage.py                    # Django管理脚本
├── requirements.txt             # 项目依赖
└── db.sqlite3                   # SQLite数据库
```

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 数据库迁移
```bash
python manage.py migrate
```

### 3. 创建超级用户
```bash
python manage.py createsuperuser
```

### 4. 运行开发服务器
```bash
python manage.py runserver
```

访问：
- API: http://localhost:8000/api/
- 管理后台: http://localhost:8000/admin/

## 技术栈

- Django 3.2.4
- pya2ldb 0.17.5
- openpyxl 3.0.7
- xlrd 1.2.0
- lxml 4.9.3

## 项目变更记录

### 2026-01-05
- ✅ 创建Django项目和hexparser应用
- ✅ 将外层models.py迁移到hexparser应用
- ✅ 创建hexparser/utils目录，整理工具类
- ✅ 配置Django管理后台
- ✅ 完成数据库迁移
