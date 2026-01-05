# pyHex 使用示例

## 启动项目

```bash
# 进入项目目录
cd /workspaces/pyHex

# 启动Django开发服务器
python manage.py runserver 0.0.0.0:8000
```

## 访问管理后台

1. 创建超级用户：
```bash
python manage.py createsuperuser
```

2. 访问管理后台：
```
http://localhost:8000/admin/
```

3. 可管理的模型：
   - A2L文件管理
   - 数据文件管理
   - 工作包管理
   - 标定参数管理
   - 测量量管理
   - 转换方法管理
   - HEX数据管理

## 使用工具类

### 1. 解析HEX文件

```python
from hexparser.utils.hex_client import HexClient
from hexparser.models import DataFile, A2LFile

# 创建或获取数据文件记录
data_file = DataFile.objects.create(
    name="test.hex",
    file_path="/path/to/test.hex",
    file_type="HEX"
)

# 创建或获取A2L文件记录
a2l_file = A2LFile.objects.create(
    name="test.a2l",
    file_path="/path/to/test.a2l"
)

# 使用HexClient解析
hex_client = HexClient()
# ... 解析逻辑
```

### 2. 解析A2L文件

```python
from hexparser.utils.a2l_client import A2LClient

# 创建A2L客户端
a2l_client = A2LClient()

# 解析A2L文件
# ... 解析逻辑
```

### 3. 导入A2L数据到数据库

```python
from hexparser.utils.a2l_importer import A2LDataImporter
from hexparser.models import A2LFile

# 创建A2L文件记录
a2l_file = A2LFile.objects.create(
    name="example.a2l",
    file_path="/path/to/example.a2l"
)

# 导入数据
importer = A2LDataImporter()
# ... 导入逻辑
```

### 4. Excel转CFG

```python
from hexparser.utils.excel_to_cfg_converter import ExcelToCfgConverter

# 创建转换器
converter = ExcelToCfgConverter()

# 转换Excel文件为CFG
# converter.convert(excel_file_path, cfg_file_path)
```

## 数据库操作示例

### 创建工作包

```python
from hexparser.models import WorkPackage

# 创建工作包
work_package = WorkPackage.objects.create(
    name="电池管理系统标定",
    parent_id=0,
    owner="张三",
    remark="BMS标定工作包"
)
```

### 查询标定参数

```python
from hexparser.models import Characteristic

# 查询所有标定参数
characteristics = Characteristic.objects.all()

# 按类型查询
value_chars = Characteristic.objects.filter(characteristic_type='VALUE')
map_chars = Characteristic.objects.filter(characteristic_type='MAP')

# 按工作包查询
wp_chars = Characteristic.objects.filter(work_package=work_package)

# 查询关键标定量
key_chars = Characteristic.objects.filter(is_key=True)
```

### 查询测量量

```python
from hexparser.models import Measurement

# 查询所有测量量
measurements = Measurement.objects.all()

# 按数据类型查询
float_measurements = Measurement.objects.filter(datatype='FLOAT32_IEEE')
```

### 查询HEX数据

```python
from hexparser.models import Hex

# 查询特定标定参数的HEX数据
hex_data = Hex.objects.filter(characteristic=characteristic)

# 查询特定HEX文件的数据
hex_data = Hex.objects.filter(hex_file=data_file)

# 查询特定成熟度的数据
hex_data = Hex.objects.filter(maturity=maturity)
```

## Django Shell示例

```bash
# 启动Django Shell
python manage.py shell
```

```python
# 在Shell中执行
from hexparser.models import *

# 查看所有工作包
WorkPackage.objects.all()

# 查看统计信息
print(f"工作包数量: {WorkPackage.objects.count()}")
print(f"标定参数数量: {Characteristic.objects.count()}")
print(f"测量量数量: {Measurement.objects.count()}")
print(f"转换方法数量: {CompuMethod.objects.count()}")

# 查看不同类型的标定参数数量
from django.db.models import Count
Characteristic.objects.values('characteristic_type').annotate(count=Count('id'))
```

## API测试

### 测试API状态

```bash
curl http://localhost:8000/api/
```

响应：
```json
{
    "status": "success",
    "message": "HEX Parser API is running",
    "version": "1.0.0"
}
```

## 开发提示

1. **使用Django管理命令**：
   ```bash
   python manage.py help  # 查看所有可用命令
   ```

2. **数据库操作**：
   ```bash
   python manage.py dbshell  # 进入数据库Shell
   python manage.py showmigrations  # 查看迁移状态
   ```

3. **检查项目**：
   ```bash
   python manage.py check  # 检查项目配置
   python manage.py check --deploy  # 检查部署配置
   ```

4. **清理数据**：
   ```bash
   python manage.py flush  # 清空数据库（保留表结构）
   ```

## 注意事项

1. 工具类文件路径已从外层移到 `hexparser/utils/` 目录
2. 所有导入路径已更新为 `from hexparser.models import ...`
3. Django设置模块已更新为 `config.settings`
4. 数据库迁移文件位于 `hexparser/migrations/`
