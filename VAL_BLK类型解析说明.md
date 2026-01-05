# VAL_BLK 类型解析说明

## 概述

VAL_BLK（值块）表示一个连续数组，数组长度通过 A2L 文件中的 `NUMBER` 字段指定，ECU 中这段内存是一批连续值。

## A2L 定义示例

```a2l
/begin CHARACTERISTIC

/* Name                   */      BAL_mAs_ChargeBalCap_Cal
/* Long Identifier        */      "剩余均衡容量计算值Q2标定值"
/* Type                   */      VAL_BLK
/* ECU Address            */      0x46fe0
/* Record Layout          */      Scalar_ULONG
/* Maximum Difference     */      0
/* Conversion Method      */      BMS_CtrlBAL_CM_uint32
/* Lower Limit            */      0
/* Upper Limit            */      4294967295
/* Array Size             */
NUMBER                            280

/end CHARACTERISTIC
```

**关键信息**:
- **Type**: `VAL_BLK` - 表示连续数组
- **ECU Address**: `0x46fe0` - 起始地址
- **Record Layout**: `Scalar_ULONG` - 每个元素是 4 字节无符号整数
- **NUMBER**: `280` - 数组包含 280 个元素
- **总字节数**: 280 × 4 = 1120 字节

## 使用方法

### 方法1: 使用 `parse_hex_characteristics`（推荐）

从数据库获取标定量定义，自动解析：

```python
from utils.hex_client import parse_hex_characteristics

# 解析指定标定量
results = parse_hex_characteristics(
    a2l_id=12,
    hex_path="path/to/file.hex",
    characteristic_names=["BAL_mAs_ChargeBalCap_Cal"]
)

if results:
    char = results[0]
    print(f"名称: {char['name']}")
    print(f"地址: {char['address']}")
    print(f"数组长度: {len(char['value'])}")  # 280
    print(f"第一个值: {char['value'][0]}")
    print(f"最后一个值: {char['value'][-1]}")
    print(f"所有值: {char['value']}")
```

**返回结果**:
```python
{
    "name": "BAL_mAs_ChargeBalCap_Cal",
    "record_layout": "Scalar_ULONG",
    "characteristic_type": "VAL_BLK",
    "address": "0x46fe0",
    "address_decimal": 294880,
    "line_no": 15,
    "byte_count": 1120,
    "raw_bytes": "B8440A42...",
    "value": [12345, 67890, ..., 98765]  # 280 个元素的列表
}
```

### 方法2: 使用 `parse_hex_val_blk`（直接指定参数）

直接指定地址和参数，无需查询数据库：

```python
from utils.hex_client import parse_hex_val_blk

# 解析 VAL_BLK 数组
values = parse_hex_val_blk(
    hex_path="path/to/file.hex",
    ecu_address="0x46fe0",
    record_layout="Scalar_ULONG",
    element_count=280
)

print(f"数组长度: {len(values)}")  # 280
print(f"第一个值: {values[0]}")
print(f"最后一个值: {values[-1]}")
print(f"所有值: {values}")
```

### 方法3: 使用 `get_characteristic_address` + `parse_hex_val_blk`

先查询地址信息，再解析：

```python
from utils.hex_client import get_characteristic_address, parse_hex_val_blk

# 1. 查询标定量地址信息
address_info = get_characteristic_address(
    a2l_id=12,
    characteristic_name="BAL_mAs_ChargeBalCap_Cal"
)

if address_info:
    # 2. 解析 VAL_BLK 数组
    values = parse_hex_val_blk(
        hex_path="path/to/file.hex",
        ecu_address=address_info['address_hex'],
        record_layout=address_info['record_layout'],
        element_count=address_info['number']
    )
    
    print(f"数组长度: {len(values)}")
    print(f"值: {values}")
```

## 支持的数据类型

`parse_hex_val_blk` 支持以下 Record Layout 类型：

| Record Layout | 字节数 | 说明 |
|--------------|--------|------|
| `Scalar_UBYTE` | 1 | 8位无符号整数 |
| `Scalar_SBYTE` | 1 | 8位有符号整数 |
| `Scalar_UWORD` | 2 | 16位无符号整数 |
| `Scalar_SWORD` | 2 | 16位有符号整数 |
| `Scalar_ULONG` | 4 | 32位无符号整数 |
| `Scalar_SLONG` | 4 | 32位有符号整数 |
| `Scalar_FLOAT32_IEEE` | 4 | 32位浮点数 |
| `Scalar_FLOAT64_IEEE` | 8 | 64位浮点数 |

## 字节序支持

默认使用小端序（Little Endian），也可以指定大端序：

```python
values = parse_hex_val_blk(
    hex_path="path/to/file.hex",
    ecu_address="0x46fe0",
    record_layout="Scalar_ULONG",
    element_count=280,
    byte_order="big"  # 大端序
)
```

## 完整示例

```python
from utils.hex_client import parse_hex_characteristics

# 解析多个 VAL_BLK 类型的标定量
characteristic_names = [
    "BAL_mAs_ChargeBalCap_Cal",
    "Another_ValBlk_Characteristic",
]

results = parse_hex_characteristics(
    a2l_id=12,
    hex_path="D:/log/calibracloud/uploads/OTHER/2025/10/1760076706451.hex",
    characteristic_names=characteristic_names
)

for char in results:
    print(f"\n{'='*80}")
    print(f"名称: {char['name']}")
    print(f"类型: {char['characteristic_type']}")
    print(f"地址: {char['address']}")
    print(f"记录布局: {char['record_layout']}")
    print(f"字节数: {char['byte_count']}")
    
    if isinstance(char['value'], list):
        print(f"数组长度: {len(char['value'])}")
        print(f"前5个值: {char['value'][:5]}")
        print(f"后5个值: {char['value'][-5:]}")
    else:
        print(f"值: {char['value']}")
```

## 注意事项

1. **地址范围**: 确保 HEX 文件中包含从起始地址开始的连续数据
   - 对于 `BAL_mAs_ChargeBalCap_Cal`: 需要从 `0x46fe0` 开始至少 1120 字节的连续数据

2. **内存对齐**: VAL_BLK 是连续数组，数据在内存中是连续存储的

3. **数据类型匹配**: 确保 `record_layout` 与实际存储的数据类型匹配

4. **元素个数**: `NUMBER` 字段必须大于 0，且与实际数组长度一致

5. **负数地址**: 如果 ECU 地址为负数（如 `-0xfdc55c48`），代码会自动处理

## 错误处理

```python
try:
    values = parse_hex_val_blk(
        hex_path="path/to/file.hex",
        ecu_address="0x46fe0",
        record_layout="Scalar_ULONG",
        element_count=280
    )
except FileNotFoundError as e:
    print(f"HEX文件不存在: {e}")
except KeyError as e:
    print(f"地址不在HEX数据范围内: {e}")
except ValueError as e:
    print(f"参数错误: {e}")
```

## 性能考虑

- `parse_hex_characteristics`: 适合批量解析多个标定量，会缓存 HEX 文件对象
- `parse_hex_val_blk`: 每次调用都会重新打开 HEX 文件，适合单次解析

对于大量 VAL_BLK 类型的解析，建议使用 `parse_hex_characteristics`。

