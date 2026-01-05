from __future__ import annotations

import dataclasses
import logging
import os
import struct
import sys
from bisect import bisect_right
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# 确保 Django 环境已初始化（用于直接运行脚本时）
def _ensure_django_setup():
    """确保 Django 环境已初始化"""
    try:
        from django.conf import settings
        if not settings.configured:
            # 设置 Django 环境
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
            # 添加项目根目录到 Python 路径
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            # 初始化 Django
            import django
            django.setup()
    except ImportError:
        # 如果 Django 未安装，跳过初始化
        pass

# 尝试初始化 Django（如果尚未初始化）
_ensure_django_setup()

# Django 相关导入必须在初始化之后
from django.db.models import Q  # noqa: E402
from django.db import transaction  # noqa: E402

from hexparser.models import Characteristic, AxisPts, AxisDescr, AxisPtsRef, Hex, DataFile, Maturity, A2LFile  # noqa: E402

logger = logging.getLogger(__name__)

# 配置日志文件输出（已禁用，问题已解决）
# def _setup_log_file_handler():
#     """设置日志文件处理器，将日志输出到项目根目录下的 test.log 文件"""
#     try:
#         # 获取项目根目录
#         project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#         log_file_path = os.path.join(project_root, 'test.log')
#         
#         # 创建文件处理器
#         file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
#         file_handler.setLevel(logging.DEBUG)  # 记录所有级别的日志（DEBUG, INFO, WARNING, ERROR）
#         
#         # 设置格式
#         formatter = logging.Formatter(
#             '%(asctime)s %(process)d/%(thread)d %(levelname)+8s %(filename)+32s:%(lineno)-5d %(funcName)-32s %(message)s',
#             datefmt="%Y/%m/%d %H:%M:%S"
#         )
#         file_handler.setFormatter(formatter)
#         
#         # 添加到 logger（如果还没有添加过）
#         # 检查是否已经存在相同路径的文件处理器
#         log_file_path_abs = os.path.abspath(log_file_path)
#         has_file_handler = False
#         for h in logger.handlers:
#             if isinstance(h, logging.FileHandler):
#                 try:
#                     if os.path.abspath(h.baseFilename) == log_file_path_abs:
#                         has_file_handler = True
#                         break
#                 except (AttributeError, Exception):
#                     pass
#         
#         if not has_file_handler:
#             logger.addHandler(file_handler)
#             logger.setLevel(logging.DEBUG)
#             # 使用 print 而不是 logger.info，因为此时 logger 可能还没有完全配置好
#             print(f"日志文件处理器已配置: {log_file_path}")
#     except Exception as e:
#         # 如果配置日志文件失败，不影响主流程
#         print(f"配置日志文件失败: {e}")

# 初始化日志文件处理器（已禁用，问题已解决）
# _setup_log_file_handler()


class HexParseError(RuntimeError):
    """Raised when the HEX file violates the Intel HEX specification."""


@dataclasses.dataclass(frozen=True)
class HexRecord:
    line_no: int
    byte_count: int
    offset_addr: int
    record_type: int
    data: bytes
    checksum: int
    base_address: int

    @property
    def end_address(self) -> int:
        return self.base_address + self.byte_count


class IntelHexFile:
    """Minimal Intel HEX parser that focuses on DATA records (type 00)."""

    def __init__(self, file_path: str) -> None:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"HEX 文件不存在: {file_path}")
        self.file_path = file_path
        self._records: List[HexRecord] = []
        self._record_starts: List[int] = []
        self._load()

    def _load(self) -> None:
        current_upper = 0
        with open(self.file_path, "r", encoding="utf-8") as hex_file:
            for idx, raw_line in enumerate(hex_file, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                if not line.startswith(":"):
                    raise HexParseError(f"第 {idx} 行不是有效的 Intel HEX 记录：{line[:20]}...")

                payload = line[1:]
                if len(payload) < 10 or len(payload) % 2 != 0:
                    raise HexParseError(f"第 {idx} 行长度不符合 HEX 规则：{line}")

                byte_count = int(payload[0:2], 16)
                offset_addr = int(payload[2:6], 16)
                record_type = int(payload[6:8], 16)
                data_hex = payload[8:-2]
                checksum = int(payload[-2:], 16)
                data_bytes = bytes.fromhex(data_hex) if data_hex else b""

                if len(data_bytes) != byte_count:
                    raise HexParseError(f"第 {idx} 行的数据长度与声明不一致：{line}")

                if record_type == 0x04:  # Extended Linear Address
                    if byte_count != 2:
                        raise HexParseError(f"第 {idx} 行的扩展地址段长度错误：{line}")
                    current_upper = int.from_bytes(data_bytes, "big")
                    continue

                if record_type != 0x00:
                    # 其他记录类型（EOF、起始线性地址等）此处不需要
                    continue

                base_address = (current_upper << 16) + offset_addr
                self._records.append(
                    HexRecord(
                        line_no=idx,
                        byte_count=byte_count,
                        offset_addr=offset_addr,
                        record_type=record_type,
                        data=data_bytes,
                        checksum=checksum,
                        base_address=base_address,
                    )
                )

        self._records.sort(key=lambda record: record.base_address)
        self._record_starts = [record.base_address for record in self._records]

    def iter_records(self) -> Iterable[HexRecord]:
        return iter(self._records)

    def fetch_bytes(self, address: int, size: int) -> Tuple[bytes, int]:
        """Fetch a continuous range of bytes starting at the absolute address."""
        if size <= 0:
            raise ValueError("size 必须大于 0")

        cursor = address
        remaining = size
        chunks: List[bytes] = []
        first_line = -1

        idx = bisect_right(self._record_starts, cursor) - 1
        idx = max(idx, 0)

        while remaining > 0 and idx < len(self._records):
            record = self._records[idx]
            if cursor < record.base_address:
                break
            if cursor >= record.end_address:
                idx += 1
                continue

            offset = cursor - record.base_address
            take = min(remaining, record.byte_count - offset)
            if first_line == -1:
                first_line = record.line_no
            chunks.append(record.data[offset : offset + take])

            cursor += take
            remaining -= take

            if cursor >= record.end_address:
                idx += 1

        if remaining > 0:
            raise KeyError(f"地址 0x{address:X} (长度 {size}) 不在 HEX 数据的连续范围内")

        return b"".join(chunks), first_line


class RecordLayoutDecoder:
    """Decode raw bytes based on record layout naming conventions."""

    _STRUCT_MAP: Dict[str, str] = {
        # 基础类型名称（向后兼容）
        "FLOAT64": "<d",
        "FLOAT32": "<f",
        "ULONG": "<I",
        "SLONG": "<i",
        "UWORD": "<H",
        "SWORD": "<h",
        "UBYTE": "<B",
        "SBYTE": "<b",
        # 标准IEEE浮点类型
        "FLOAT32_IEEE": "<f",
        "FLOAT64_IEEE": "<d",
        # 布尔类型（通常用 UBYTE 表示，1 字节，0 或 1）
        "BOOLEAN": "<B",  # 布尔类型，使用无符号字节
        "SCALAR_BOOLEAN": "<B",  # 标量布尔类型
        # LONG 类型（32位有符号整数）
        "SCALAR_LONG": "<i",  # 标量 LONG 类型，32位有符号整数（等同于 SLONG）
        # Lookup 类型（用于 MAP/CURVE）
        "LOOKUP1D_FLOAT32_IEEE": "<f",  # CURVE/MAP 的 Y 轴数据
        "LOOKUP1D_X_FLOAT32_IEEE": "<f",  # CURVE/MAP 的 X 轴数据
        "LOOKUP2D_FLOAT32_IEEE": "<f",  # MAP 的 Z 轴数据（二维矩阵）
        "LOOKUP2D_X_FLOAT32_IEEE": "<f",  # MAP 的 X/Y 轴数据
    }

    def __init__(self, record_layout: str) -> None:
        if not record_layout:
            raise KeyError("record_layout 不能为空")
        self.record_layout = record_layout
        self.struct_format = self._resolve_struct(record_layout)
        self._struct = struct.Struct(self.struct_format)

    @staticmethod
    def _resolve_struct(record_layout: str) -> str:
        """解析记录布局字符串，返回对应的struct格式。
        
        按关键字长度降序匹配，确保更长的关键字（如 FLOAT32_IEEE）优先于短的关键字（如 FLOAT32）。
        这样可以正确处理 Scalar_FLOAT32_IEEE、Array_FLOAT32_IEEE 等标准类型名称。
        """
        upper_layout = record_layout.upper()
        # 按关键字长度降序排序，优先匹配更长的关键字
        sorted_items = sorted(
            RecordLayoutDecoder._STRUCT_MAP.items(),
            key=lambda x: len(x[0]),
            reverse=True
        )
        for keyword, fmt in sorted_items:
            if keyword in upper_layout:
                return fmt
        raise KeyError(f"暂不支持的 Record Layout: {record_layout}")

    @property
    def element_size(self) -> int:
        return self._struct.size

    def decode(self, data: bytes) -> float | int:
        if len(data) != self._struct.size:
            raise ValueError(f"需要 {self._struct.size} 字节，但收到 {len(data)} 字节")
        return self._struct.unpack(data)[0]

    def decode_many(self, data: bytes, count: int) -> List[float | int]:
        expected = self.element_size * count
        if len(data) != expected:
            raise ValueError(f"需要 {expected} 字节，但收到 {len(data)} 字节")
        return [
            self.decode(data[i : i + self.element_size])
            for i in range(0, expected, self.element_size)
        ]


def _normalize_characteristic_names(names: Optional[Sequence[str]]) -> Optional[List[str]]:
    if not names:
        return None
    normalized = [str(name).strip() for name in names if str(name).strip()]
    return normalized or None


def _normalize_characteristic_number(value: object, characteristic_type: str = "VALUE") -> int:
    """Normalize the CHARACTERISTIC 'number' field. 
    
    根据模型定义：
    - VALUE 类型：number 默认为 0，应视为 1（单个值）
    - 非 VALUE 类型（MAP/CURVE/VAL_BLK）：number 必须大于 0
    
    Raises ValueError if unable to parse.
    """
    if value is None:
        # VALUE 类型默认为 1，其他类型需要明确指定
        if characteristic_type == "VALUE":
            return 1
        raise ValueError("Characteristic 'number' 字段不能为 None")
    
    if isinstance(value, (int, float)):
        result = int(value)
        # VALUE 类型：0 或 1 都视为 1
        if characteristic_type == "VALUE":
            return max(1, result) if result >= 0 else 1
        # 非 VALUE 类型：必须大于 0
        if result <= 0:
            raise ValueError(f"Characteristic 'number' 字段必须大于 0，但得到: {value}")
        return result
    
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            if characteristic_type == "VALUE":
                return 1
            raise ValueError("Characteristic 'number' 字段不能为空字符串")
        try:
            result = int(float(stripped))
            if characteristic_type == "VALUE":
                return max(1, result) if result >= 0 else 1
            if result <= 0:
                raise ValueError(f"Characteristic 'number' 字段必须大于 0，但得到: {value}")
            return result
        except ValueError as e:
            raise ValueError(f"Characteristic 'number' 字段无法解析为数字: {value}") from e
    
    # 处理 Number 对象或其他对象类型
    extracted = getattr(value, "number", None)
    if extracted is not None:
        try:
            result = int(extracted)
            if characteristic_type == "VALUE":
                return max(1, result) if result >= 0 else 1
            if result <= 0:
                raise ValueError(f"Characteristic 'number' 字段必须大于 0，但得到: {extracted}")
            return result
        except (TypeError, ValueError) as e:
            raise ValueError(f"Characteristic 'number' 字段提取失败: {value} (提取值: {extracted})") from e
    
    # 尝试直接转换为字符串再解析
    try:
        result = int(float(str(value)))
        if characteristic_type == "VALUE":
            return max(1, result) if result >= 0 else 1
        if result <= 0:
            raise ValueError(f"Characteristic 'number' 字段必须大于 0，但得到: {value}")
        return result
    except (TypeError, ValueError) as e:
        raise ValueError(f"Characteristic 'number' 字段无法解析为数字: {value} (类型: {type(value).__name__})") from e


def _fetch_characteristics_from_db(
    a2l_file_id: int, names: Optional[Sequence[str]]
) -> List[Dict[str, object]]:
    """Fetch characteristic definitions from the database for the given A2L file."""
    queryset = Characteristic.objects.filter(a2l_file_id=a2l_file_id)

    if names:
        name_filters = Q()
        for name in names:
            if not name:
                continue
            name_filters |= Q(name__iexact=name)
        if name_filters:
            queryset = queryset.filter(name_filters)
        else:
            return []

    queryset = queryset.only(
        "name", "record_layout", "characteristic_type", "ecu_address", "number"
    )

    return [
        {
            "name": characteristic.name,
            "record_layout": characteristic.record_layout,
            "characteristic_type": characteristic.characteristic_type,
            "ecu_address": characteristic.ecu_address,
            "number": characteristic.number,
        }
        for characteristic in queryset
    ]


def _guess_axis_pts_by_name(
    a2l_file_id: int,
    characteristic_name: str,
) -> Optional[AxisPts]:
    """Fallback: 根据名称猜测 CURVE 对应的 AxisPts 定义。"""
    name_parts = characteristic_name.split("_")
    candidates = {
        characteristic_name.replace("_T", "_X"),
        characteristic_name.replace("_T", "_AX"),
        characteristic_name.replace("_T", "_Rng"),
        characteristic_name.replace("Step", "Rng"),
        "_".join(name_parts[:-1]) + "_X" if len(name_parts) > 1 else None,
        "_".join(name_parts[:-1]) + "_Rng" if len(name_parts) > 1 else None,
        "".join(name_parts[:-1]) + "X" if len(name_parts) > 1 else None,
        "".join(name_parts[:-1]) + "Rng" if len(name_parts) > 1 else None,
    }
    for candidate in filter(None, candidates):
        axis_pts = AxisPts.objects.filter(
            a2l_file_id=a2l_file_id, name__iexact=candidate
        ).first()
        if axis_pts:
            return axis_pts
    base = "_".join(name_parts[:-1]) if len(name_parts) > 1 else characteristic_name
    axis_pts = AxisPts.objects.filter(
        a2l_file_id=a2l_file_id, name__icontains=base
    ).first()
    if axis_pts:
        return axis_pts
    axis_pts = AxisPts.objects.filter(
        a2l_file_id=a2l_file_id, name__icontains=base.replace("_", "")
    ).first()
    if axis_pts:
        return axis_pts
    return None


def _fetch_curve_axis_definition(
    a2l_id: int,
    characteristic_name: str,
    characteristic_id: Optional[int] = None,
) -> Optional[Dict[str, object]]:
    """获取 CURVE 类型的 X 轴 AxisPts 定义。
    
    Args:
        a2l_id: A2L 文件 ID
        characteristic_name: 标定量名称
        characteristic_id: 标定量 ID（可选，如果提供则优先使用，避免同名不同地址的记录冲突）
    """
    # 如果提供了 characteristic_id，优先使用 ID 查询（更准确）
    if characteristic_id:
        characteristic = Characteristic.objects.filter(
            id=characteristic_id,
            a2l_file_id=a2l_id,
            characteristic_type="CURVE",
        ).first()
    else:
        # 否则按名称查询（向后兼容）
        characteristic = Characteristic.objects.filter(
            a2l_file_id=a2l_id,
            name__iexact=characteristic_name,
            characteristic_type="CURVE",
        ).first()
    if not characteristic:
        return None

    # 优先通过 characteristic_id 查询 AxisDescr（最准确）
    axis_descr = (
        characteristic.characteristic_axis_descrs.first()
        or AxisDescr.objects.filter(characteristic_id=characteristic.id).first()
    )

    # 如果通过 characteristic_id 找不到 AxisDescr，可能是由于导入时关联到了错误的 Characteristic
    # 尝试通过 name 查找所有同名的 Characteristic，然后查找它们关联的 AxisDescr
    if not axis_descr:
        logger.debug("CURVE %s (ID=%s) 通过 characteristic_id 未找到 AxisDescr，尝试通过 name 查找", 
                    characteristic_name, characteristic.id)
        # 查找所有同名的 Characteristic（可能由于 unique_together 包含 ecu_address 和 conversion_method 而有多个）
        same_name_chars = Characteristic.objects.filter(
            a2l_file_id=a2l_id,
            name__iexact=characteristic_name,
            characteristic_type="CURVE",
        )
        
        if same_name_chars.exists():
            # 收集所有同名 Characteristic 的 ID
            char_ids = list(same_name_chars.values_list('id', flat=True))
            logger.debug("CURVE %s 找到 %d 个同名的 Characteristic (IDs: %s)", 
                       characteristic_name, len(char_ids), char_ids)
            
            # 查找这些 Characteristic 关联的所有 AxisDescr
            all_axis_descrs = list(AxisDescr.objects.filter(characteristic_id__in=char_ids).all())
            
            if all_axis_descrs:
                logger.info("CURVE %s 通过 name 查找找到 %d 个 AxisDescr 记录（来自 %d 个同名 Characteristic）", 
                           characteristic_name, len(all_axis_descrs), len(char_ids))
                
                # 优先选择第一个 Characteristic 的 AxisDescr
                first_char_id = char_ids[0]
                axis_descr = next((ad for ad in all_axis_descrs if ad.characteristic_id == first_char_id), None)
                
                # 如果第一个 Characteristic 没有 AxisDescr，使用第一个找到的 AxisDescr
                if not axis_descr:
                    axis_descr = all_axis_descrs[0]
                    logger.warning("CURVE %s 第一个 Characteristic (ID=%s) 没有 AxisDescr，使用第一个找到的 AxisDescr (char_id=%s)", 
                                 characteristic_name, first_char_id, axis_descr.characteristic_id)
            else:
                logger.warning("CURVE %s 同名 Characteristic 都没有关联的 AxisDescr 记录", characteristic_name)
        else:
            logger.warning("CURVE %s 未找到任何同名的 Characteristic", characteristic_name)

    axis_pts: Optional[AxisPts] = None
    if axis_descr:
        axis_pts_ref = (
            axis_descr.axis_pts_ref.first()
            or AxisPtsRef.objects.filter(axis_descr_id=axis_descr.id).first()
        )
        if axis_pts_ref:
            axis_pts = AxisPts.objects.filter(
                a2l_file_id=a2l_id,
                name__iexact=axis_pts_ref.axis_points,
            ).first()
            if not axis_pts:
                axis_pts = AxisPts.objects.filter(
                    name__iexact=axis_pts_ref.axis_points
                ).first()

    if axis_pts is None:
        axis_pts = _guess_axis_pts_by_name(a2l_id, characteristic_name)

    if axis_pts is None:
        return None

    return {
        "name": axis_pts.name,
        "ecu_address": axis_pts.address,
        "record_layout": axis_pts.record_layout,
        "max_axis_points": axis_pts.max_axis_points,
    }


def _fetch_map_axis_definitions(
    a2l_id: int,
    characteristic_name: str,
    characteristic_id: Optional[int] = None,
) -> Optional[Dict[str, Dict[str, object]]]:
    """获取 MAP 类型的 X 轴和 Y 轴 AxisPts 定义。
    
    MAP 类型有两个轴：X 轴和 Y 轴，分别对应第一个和第二个 AXIS_DESCR。
    
    Args:
        a2l_id: A2L 文件 ID
        characteristic_name: 标定量名称
        characteristic_id: 标定量 ID（可选，如果提供则优先使用，避免同名不同地址的记录冲突）
    
    Returns:
        包含 'x_axis' 和 'y_axis' 键的字典，如果未找到则返回 None
    """
    # 如果提供了 characteristic_id，优先使用 ID 查询（更准确）
    if characteristic_id:
        characteristic = Characteristic.objects.filter(
            id=characteristic_id,
            a2l_file_id=a2l_id,
            characteristic_type="MAP",
        ).first()
    else:
        # 否则按名称查询（向后兼容）
        characteristic = Characteristic.objects.filter(
            a2l_file_id=a2l_id,
            name__iexact=characteristic_name,
            characteristic_type="MAP",
        ).first()
    if not characteristic:
        return None

    # 获取所有 AxisDescr（MAP 类型通常有两个：X 轴和 Y 轴）
    # 优先通过 characteristic_id 查询（最准确）
    axis_descrs = list(
        characteristic.characteristic_axis_descrs.all()
        or AxisDescr.objects.filter(characteristic_id=characteristic.id).all()
    )

    # 如果通过 characteristic_id 找不到 AxisDescr，可能是由于导入时关联到了错误的 Characteristic
    # 尝试通过 name 查找所有同名的 Characteristic，然后查找它们关联的 AxisDescr
    if not axis_descrs:
        logger.debug("MAP %s (ID=%s) 通过 characteristic_id 未找到 AxisDescr，尝试通过 name 查找", 
                    characteristic_name, characteristic.id)
        # 查找所有同名的 Characteristic（可能由于 unique_together 包含 ecu_address 和 conversion_method 而有多个）
        same_name_chars = Characteristic.objects.filter(
            a2l_file_id=a2l_id,
            name__iexact=characteristic_name,
            characteristic_type="MAP",
        )
        
        if same_name_chars.exists():
            # 收集所有同名 Characteristic 的 ID
            char_ids = list(same_name_chars.values_list('id', flat=True))
            logger.debug("MAP %s 找到 %d 个同名的 Characteristic (IDs: %s)", 
                       characteristic_name, len(char_ids), char_ids)
            
            # 查找这些 Characteristic 关联的所有 AxisDescr
            all_axis_descrs = list(AxisDescr.objects.filter(characteristic_id__in=char_ids).all())
            
            if all_axis_descrs:
                logger.info("MAP %s 通过 name 查找找到 %d 个 AxisDescr 记录（来自 %d 个同名 Characteristic）", 
                           characteristic_name, len(all_axis_descrs), len(char_ids))
                
                # 如果找到多个同名 Characteristic，优先选择与当前 characteristic 最匹配的 AxisDescr
                # 这里我们选择第一个 Characteristic 的 AxisDescr（通常是导入时创建的）
                # 或者，我们可以尝试通过其他字段（如 attribute）来匹配
                # 但为了简单起见，我们先使用第一个 Characteristic 的 AxisDescr
                first_char_id = char_ids[0]
                axis_descrs = [ad for ad in all_axis_descrs if ad.characteristic_id == first_char_id]
                
                # 如果第一个 Characteristic 没有 AxisDescr，使用所有找到的 AxisDescr
                if not axis_descrs:
                    axis_descrs = all_axis_descrs
                    logger.warning("MAP %s 第一个 Characteristic (ID=%s) 没有 AxisDescr，使用所有找到的 AxisDescr", 
                                 characteristic_name, first_char_id)
            else:
                logger.warning("MAP %s 同名 Characteristic 都没有关联的 AxisDescr 记录", characteristic_name)
        else:
            logger.warning("MAP %s 未找到任何同名的 Characteristic", characteristic_name)

    debug_axis_info: List[str] = []
    for idx, axis_descr in enumerate(axis_descrs):
        attribute = getattr(axis_descr, "attribute", "")
        axis_pts_ref = (
            axis_descr.axis_pts_ref.first()
            or AxisPtsRef.objects.filter(axis_descr_id=axis_descr.id).first()
        )
        axis_pts_name = axis_pts_ref.axis_points if axis_pts_ref else None
        char_id = getattr(axis_descr, "characteristic_id", None)
        debug_axis_info.append(
            f"[{idx}] char_id={char_id}, attribute={attribute}, axis_pts_ref={axis_pts_name}"
        )

    if debug_axis_info:
        logger.info("MAP %s AxisDescr info: %s", characteristic_name, "; ".join(debug_axis_info))
    else:
        logger.warning("MAP %s AxisDescr info: [] (未找到 AxisDescr 记录)", characteristic_name)
    
    if not axis_descrs:
        logger.warning("MAP %s (ID=%s) 未找到任何 AxisDescr 记录", characteristic_name, characteristic.id)
        return None
    
    if len(axis_descrs) < 2:
        logger.warning("MAP %s 的轴定义数量不足: %d (需要至少2个，当前找到: %s)", 
                     characteristic_name, len(axis_descrs), 
                     ", ".join([f"ID={ad.id}, attribute={getattr(ad, 'attribute', '')}" for ad in axis_descrs]))
        return None

    # 第一个 AxisDescr 是 X 轴，第二个是 Y 轴
    x_axis_descr = axis_descrs[0]
    y_axis_descr = axis_descrs[1]

    def _get_axis_pts_from_descr(axis_descr: AxisDescr, axis_index: int = 0) -> Optional[Dict[str, object]]:
        """从 AxisDescr 获取对应的 AxisPts 定义（复用 CURVE 的逻辑）。
        
        Args:
            axis_descr: AxisDescr 对象
            axis_index: 轴索引（0=X轴, 1=Y轴），用于回退查找
        """
        axis_pts_ref = (
            axis_descr.axis_pts_ref.first()
            or AxisPtsRef.objects.filter(axis_descr_id=axis_descr.id).first()
        )
        
        # 如果当前 AxisDescr 没有 AxisPtsRef，尝试通过 Characteristic 查找所有 AxisPtsRef，然后按顺序匹配
        if not axis_pts_ref:
            logger.warning("MAP %s 的 AxisDescr (ID=%s, attribute=%s, index=%d) 未找到 AxisPtsRef，尝试通过 Characteristic 查找", 
                         characteristic_name, axis_descr.id, getattr(axis_descr, "attribute", ""), axis_index)
            
            # 查找该 Characteristic 的所有 AxisDescr 及其关联的 AxisPtsRef
            all_axis_descrs = list(AxisDescr.objects.filter(characteristic_id=characteristic.id).order_by('id'))
            all_axis_pts_refs = []
            for ad in all_axis_descrs:
                refs = list(AxisPtsRef.objects.filter(axis_descr_id=ad.id).all())
                all_axis_pts_refs.extend([(ad.id, ref) for ref in refs])
            
            if all_axis_pts_refs:
                logger.info("MAP %s 找到 %d 个 AxisPtsRef（来自 %d 个 AxisDescr），尝试按索引 %d 匹配", 
                           characteristic_name, len(all_axis_pts_refs), len(all_axis_descrs), axis_index)
                
                # 如果索引在范围内，使用对应位置的 AxisPtsRef
                if axis_index < len(all_axis_pts_refs):
                    _, axis_pts_ref = all_axis_pts_refs[axis_index]
                    logger.info("MAP %s 通过索引匹配找到 AxisPtsRef: axis_points=%s (AxisDescr ID=%s)", 
                               characteristic_name, axis_pts_ref.axis_points, axis_pts_ref.axis_descr_id)
                else:
                    # 如果索引超出范围，使用最后一个 AxisPtsRef
                    _, axis_pts_ref = all_axis_pts_refs[-1]
                    logger.warning("MAP %s 索引 %d 超出范围，使用最后一个 AxisPtsRef: axis_points=%s (AxisDescr ID=%s)", 
                                 characteristic_name, axis_index, axis_pts_ref.axis_points, axis_pts_ref.axis_descr_id)
            else:
                logger.warning("MAP %s 的 Characteristic (ID=%s) 没有任何 AxisPtsRef 记录", 
                             characteristic_name, characteristic.id)
                return None
        
        axis_pts_name = axis_pts_ref.axis_points
        # logger.info("MAP %s 查找 AxisPts: name=%s (A2L ID=%s, AxisPtsRef ID=%s)", 
        #             characteristic_name, axis_pts_name, a2l_id, axis_pts_ref.id)
        
        # 优先在当前 A2L 文件中查找
        axis_pts = AxisPts.objects.filter(
            a2l_file_id=a2l_id,
            name__iexact=axis_pts_name,
        ).first()
        
        if not axis_pts:
            # 如果当前 A2L 文件中没找到，尝试在其他 A2L 文件中查找（向后兼容）
            logger.warning("MAP %s 在当前 A2L 文件中未找到 AxisPts (name=%s, A2L ID=%s)，尝试全局查找", 
                        characteristic_name, axis_pts_name, a2l_id)
            axis_pts = AxisPts.objects.filter(
                name__iexact=axis_pts_name
            ).first()
            if axis_pts:
                logger.info("MAP %s 在全局范围内找到 AxisPts (name=%s, A2L ID=%s, 实际A2L ID=%s)", 
                           characteristic_name, axis_pts_name, a2l_id, axis_pts.a2l_file_id)
        
        if not axis_pts:
            logger.warning("MAP %s 未找到 AxisPts (name=%s, A2L ID=%s)。请检查 A2L 文件是否已正确解析 AxisPts 记录", 
                         characteristic_name, axis_pts_name, a2l_id)
            return None
        
        # 验证必要的字段
        if not axis_pts.address or not axis_pts.record_layout:
            logger.warning("MAP %s 的 AxisPts (name=%s, ID=%s) 缺少必要字段: address=%s, record_layout=%s", 
                         characteristic_name, axis_pts.name, axis_pts.id, axis_pts.address, axis_pts.record_layout)
            return None
        
        logger.debug("MAP %s 找到 AxisPts: name=%s, address=0x%X, record_layout=%s, max_axis_points=%s", 
                    characteristic_name, axis_pts.name, axis_pts.address, axis_pts.record_layout, axis_pts.max_axis_points)
        
        return {
            "name": axis_pts.name,
            "ecu_address": axis_pts.address,
            "record_layout": axis_pts.record_layout,
            "max_axis_points": axis_pts.max_axis_points,
        }

    x_axis = _get_axis_pts_from_descr(x_axis_descr, axis_index=0)
    y_axis = _get_axis_pts_from_descr(y_axis_descr, axis_index=1)

    if not x_axis or not y_axis:
        x_status = "存在" if x_axis else "缺失"
        y_status = "存在" if y_axis else "缺失"
        logger.warning("MAP %s 的 X/Y 轴定义不完整: x_axis=%s, y_axis=%s", 
                     characteristic_name, x_status, y_status)
        if not x_axis:
            logger.warning("MAP %s 的 X 轴 (AxisDescr ID=%s, attribute=%s) 获取失败", 
                         characteristic_name, x_axis_descr.id, getattr(x_axis_descr, "attribute", ""))
        if not y_axis:
            logger.warning("MAP %s 的 Y 轴 (AxisDescr ID=%s, attribute=%s) 获取失败", 
                         characteristic_name, y_axis_descr.id, getattr(y_axis_descr, "attribute", ""))
        return None

    return {
        "x_axis": x_axis,
        "y_axis": y_axis,
    }


def _format_address(ecu_address: Optional[int]) -> Tuple[Optional[str], Optional[int]]:
    """格式化ECU地址，支持负数地址。
    
    根据模型定义，ecu_address 是 BigIntegerField，可能包含负数（如 -0xfdc55c48）。
    
    Args:
        ecu_address: ECU地址（BigInteger，可能为负数）
        
    Returns:
        (hex_string, decimal_value) 元组
        - hex_string: 十六进制字符串，负数格式为 "-0x..."，正数格式为 "0x..."
        - decimal_value: 十进制整数值（保持原始有符号值）
    """
    if ecu_address is None:
        return None, None
    
    # 处理负数地址（使用有符号64位整数）
    address_int = int(ecu_address)
    
    # 十六进制格式：负数使用 "-0x..." 格式，正数使用 "0x..." 格式
    if address_int < 0:
        hex_str = f"-0x{abs(address_int):X}"
    else:
        hex_str = f"0x{address_int:X}"
    
    return hex_str, address_int


def get_characteristic_address(
    *,
    a2l_id: int,
    characteristic_name: str,
) -> Optional[Dict[str, object]]:
    """根据标定量name从MySQL获取地址信息。
    
    Args:
        a2l_id: A2L文件ID
        characteristic_name: 标定量名称
        
    Returns:
        包含地址信息的字典，如果未找到则返回None
    """
    characteristics = _fetch_characteristics_from_db(a2l_id, [characteristic_name])
    
    if not characteristics:
        return None
    
    # 直接返回第一个匹配的结果
    char = characteristics[0]
    ecu_address = char.get("ecu_address")
    address_hex, address_decimal = _format_address(ecu_address)
    
    return {
        "name": char.get("name"),
        "ecu_address": ecu_address,
        "address_hex": address_hex,
        "address_decimal": address_decimal,
        "record_layout": char.get("record_layout"),
        "characteristic_type": char.get("characteristic_type"),
        "number": char.get("number"),
    }


def parse_hex_characteristics(
    *,
    a2l_id: int,
    hex_path: str,
    characteristic_names: Optional[Sequence[str]] = None,
    hex_file_obj: Optional["IntelHexFile"] = None,
) -> List[Dict[str, object]]:
    """Parse HEX bytes based on characteristic definitions stored in DB."""

    if not a2l_id:
        raise ValueError("a2l_id 必须提供")

    # logger.info("开始解析 HEX 特性, A2L ID: %s, HEX: %s", a2l_id, hex_path)

    characteristic_filters = None
    normalized_names = _normalize_characteristic_names(characteristic_names)
    if normalized_names:
        characteristic_filters = {name.upper() for name in normalized_names}

    characteristics = _fetch_characteristics_from_db(a2l_id, normalized_names)
    if not characteristics:
        logger.warning("A2L 文件 (id=%s) 未查询到 characteristic 定义", a2l_id)
        return []

    hex_file = hex_file_obj or IntelHexFile(hex_path)
    results: List[Dict[str, object]] = []

    # total_chars = len(characteristics)
    for idx, characteristic in enumerate(characteristics, start=1):
        name = characteristic.get("name")
        if not name:
            continue
        if characteristic_filters and name.upper() not in characteristic_filters:
            continue

        # if characteristic_filters:
        #     logger.info(
        #         "parse characteristic value [%d/%d]: %s",
        #         idx,
        #         total_chars,
        #         name,
        #     )

        record_layout = characteristic.get("record_layout")
        ecu_address = characteristic.get("ecu_address")
        if record_layout is None or ecu_address is None:
            logger.warning("标定量 %s 缺少必要字段: record_layout=%s, ecu_address=%s", name, record_layout, ecu_address)
            continue

        try:
            address_int = int(ecu_address)
        except (TypeError, ValueError):
            logger.warning("标定量 %s 的地址无效: %s，已跳过", name, ecu_address)
            continue

        if address_int == 0:
            logger.info("标定量 %s 的 ecu_address 为 0，跳过 HEX 解析", name)
            continue

        try:
            decoder = RecordLayoutDecoder(record_layout)
        except KeyError:
            logger.warning("标定量 %s (地址=0x%X) 的记录布局暂不支持: %s", name, address_int, record_layout)
            continue

        try:
            characteristic_type = characteristic.get("characteristic_type", "VALUE")
            element_count = _normalize_characteristic_number(
                characteristic.get("number"), 
                characteristic_type=characteristic_type
            )
        except ValueError as e:
            logger.warning("跳过标定量 %s: number 字段无效 (%s)", name, str(e))
            continue

        try:
            # 如果地址为负数，需要转换为无符号地址用于HEX文件查找
            # HEX文件中的地址通常是无符号的，负数地址需要特殊处理
            if address_int < 0:
                # 将负数地址转换为无符号64位地址
                unsigned_address = address_int & 0xFFFFFFFFFFFFFFFF
            else:
                unsigned_address = address_int
            
            raw_bytes, line_no = hex_file.fetch_bytes(unsigned_address, decoder.element_size * element_count)
        except KeyError:
            logger.warning("地址 0x%X 不在 HEX 数据范围内: name=%s", address_int, name)
            continue

        decoded_values = decoder.decode_many(raw_bytes, element_count)
        value = decoded_values[0] if len(decoded_values) == 1 else decoded_values

        # 格式化地址显示
        address_hex, address_decimal = _format_address(int(ecu_address))
        
        results.append(
            {
                "name": name,
                "record_layout": record_layout,
                "characteristic_type": characteristic.get("characteristic_type"),
                "address": address_hex,
                "address_decimal": address_decimal,
                "line_no": line_no,
                "byte_count": len(raw_bytes),
                "raw_bytes": raw_bytes.hex().upper(),
                "value": value,
            }
        )

    return results


def parse_hex_address_to_value(
    hex_path: str,
    ecu_address: str | int,
    data_type: str = "FLOAT32",
    byte_order: str = "little",
) -> float:
    """根据ECU地址从HEX文件解析实际物理值。
    
    根据 hex_parse_rules.md 中的解析规则：
    - 地址 0x40314 -> 从HEX文件中读取对应地址的字节数据
    - 按指定数据类型（默认FLOAT32）和小端序解析为物理值
    
    Args:
        hex_path: HEX文件路径
        ecu_address: ECU地址，可以是字符串 "0x40314" 或整数 262932
        data_type: 数据类型，支持：
            - 基础类型: "FLOAT32"（默认）、"FLOAT64"、"ULONG"、"SLONG"、"UWORD"、"SWORD"、"UBYTE"、"SBYTE"
            - 标量类型: "Scalar_FLOAT32_IEEE"、"Scalar_ULONG" 等
            - 数组类型: "Array_UBYTE"、"Array_FLOAT32_IEEE" 等
            - 映射表类型: "Map_FLOAT32_IEEE"、"Map_UWORD" 等
            - IEEE类型: "FLOAT32_IEEE"、"FLOAT64_IEEE"
        byte_order: 字节序，"little"（小端，默认）或 "big"（大端）
    
    Returns:
        float | int: 解析后的物理值
    
    Raises:
        FileNotFoundError: HEX文件不存在
        KeyError: 地址不在HEX数据范围内
        ValueError: 数据类型不支持或地址格式错误
    
    Example:
        >>> value = parse_hex_address_to_value("path/to/file.hex", "0x40314")
        >>> print(value)  # 3500.0
    """
    # 解析地址
    if isinstance(ecu_address, str):
        if ecu_address.startswith("0x") or ecu_address.startswith("0X"):
            address_int = int(ecu_address, 16)
        else:
            address_int = int(ecu_address)
    else:
        address_int = int(ecu_address)
    
    # 确定数据类型和字节数
    # 支持标准类型定义（参考 models.py RECORD_LAYOUT_CHOICES）
    type_map = {
        # === 基础类型（向后兼容）===
        "FLOAT32": ("<f", 4),  # 小端 float32
        "FLOAT64": ("<d", 8),  # 小端 float64
        "ULONG": ("<I", 4),     # 小端 uint32
        "SLONG": ("<i", 4),     # 小端 int32
        "UWORD": ("<H", 2),     # 小端 uint16
        "SWORD": ("<h", 2),     # 小端 int16
        "UBYTE": ("<B", 1),     # uint8
        "SBYTE": ("<b", 1),     # int8
        # === 标量类型 ===
        "SCALAR_UBYTE": ("<B", 1),           # 8位无符号整数
        "SCALAR_SBYTE": ("<b", 1),           # 8位有符号整数
        "SCALAR_UWORD": ("<H", 2),            # 16位无符号整数
        "SCALAR_SWORD": ("<h", 2),           # 16位有符号整数
        "SCALAR_ULONG": ("<I", 4),           # 32位无符号整数
        "SCALAR_SLONG": ("<i", 4),           # 32位有符号整数
        "SCALAR_FLOAT32_IEEE": ("<f", 4),    # 32位浮点数
        "SCALAR_FLOAT64_IEEE": ("<d", 8),   # 64位浮点数
        # === 数组类型（底层数据类型相同）===
        "ARRAY_UBYTE": ("<B", 1),           # 8位无符号整数数组
        "ARRAY_SBYTE": ("<b", 1),           # 8位有符号整数数组
        "ARRAY_UWORD": ("<H", 2),           # 16位无符号整数数组
        "ARRAY_SWORD": ("<h", 2),           # 16位有符号整数数组
        "ARRAY_ULONG": ("<I", 4),           # 32位无符号整数数组
        "ARRAY_SLONG": ("<i", 4),           # 32位有符号整数数组
        "ARRAY_FLOAT32_IEEE": ("<f", 4),    # 32位浮点数组
        "ARRAY_FLOAT64_IEEE": ("<d", 8),    # 64位浮点数组
        # === 映射表类型（MAP / CURVE类，底层数据类型相同）===
        "MAP_FLOAT32_IEEE": ("<f", 4),      # 32位浮点映射表
        "MAP_UBYTE": ("<B", 1),             # 8位无符号整数映射表
        "MAP_SBYTE": ("<b", 1),             # 8位有符号整数映射表
        "MAP_UWORD": ("<H", 2),             # 16位无符号整数映射表
        "MAP_SWORD": ("<h", 2),             # 16位有符号整数映射表
        # === IEEE浮点类型（简化形式）===
        "FLOAT32_IEEE": ("<f", 4),          # 32位浮点数（IEEE）
        "FLOAT64_IEEE": ("<d", 8),          # 64位浮点数（IEEE）
    }
    
    upper_type = data_type.upper()
    if upper_type not in type_map:
        raise ValueError(
            f"不支持的数据类型: {data_type}，支持的类型: "
            f"FLOAT32/FLOAT64/ULONG/SLONG/UWORD/SWORD/UBYTE/SBYTE, "
            f"Scalar_*/Array_*/Map_* 前缀类型, 或 FLOAT32_IEEE/FLOAT64_IEEE"
        )
    
    struct_format, byte_size = type_map[upper_type]
    
    # 如果指定了大端序，修改格式
    if byte_order.lower() == "big":
        struct_format = struct_format.replace("<", ">")
    
    # 打开HEX文件并读取数据
    hex_file = IntelHexFile(hex_path)
    
    # 处理负数地址（转换为无符号地址用于HEX文件查找）
    if address_int < 0:
        unsigned_address = address_int & 0xFFFFFFFFFFFFFFFF
    else:
        unsigned_address = address_int
    
    # 从HEX文件读取指定地址的字节数据
    raw_bytes, line_no = hex_file.fetch_bytes(unsigned_address, byte_size)
    
    # 打印匹配到的字节信息
    hex_str = raw_bytes.hex().upper()
    print(f"地址: {ecu_address} (0x{address_int:X})")
    print(f"HEX行号: {line_no}")
    print(f"读取字节数: {byte_size}")
    print(f"原始字节(十六进制): {hex_str}")
    print(f"原始字节(十进制): {', '.join(str(b) for b in raw_bytes)}")
    print(f"字节数组: [{', '.join(f'0x{b:02X}' for b in raw_bytes)}]")
    
    # 解析字节数据为物理值
    value = struct.unpack(struct_format, raw_bytes)[0]
    print(f"解析后的物理值: {value}")
    print("-" * 80)
    
    return value


def parse_hex_val_blk(
    *,
    hex_path: str,
    ecu_address: str | int,
    record_layout: str,
    element_count: int,
    byte_order: str = "little",
    hex_file_obj: Optional["IntelHexFile"] = None,
) -> List[float | int]:
    """解析 VAL_BLK 类型的连续数组数据。
    
    VAL_BLK 表示一个连续数组，数组长度通过 element_count 指定，
    ECU 中这段内存是一批连续值。
    
    Args:
        hex_path: HEX文件路径
        ecu_address: ECU地址，可以是字符串 "0x46fe0" 或整数 294880
        record_layout: 记录布局，指定每个元素的数据类型
                      支持: Scalar_ULONG, Scalar_SLONG, Scalar_UWORD, 
                           Scalar_SWORD, Scalar_UBYTE, Scalar_SBYTE,
                           Scalar_FLOAT32_IEEE, Scalar_FLOAT64_IEEE
        element_count: 数组元素个数（从 A2L 的 NUMBER 字段获取）
        byte_order: 字节序，"little"（小端，默认）或 "big"（大端）
    
    Returns:
        List[float | int]: 解析后的数组值列表
    
    Raises:
        FileNotFoundError: HEX文件不存在
        KeyError: 地址不在HEX数据范围内
        ValueError: 数据类型不支持或地址格式错误
    
    Example:
        >>> # 解析 BAL_mAs_ChargeBalCap_Cal (VAL_BLK, NUMBER=280, Scalar_ULONG)
        >>> values = parse_hex_val_blk(
        ...     hex_path="path/to/file.hex",
        ...     ecu_address="0x46fe0",
        ...     record_layout="Scalar_ULONG",
        ...     element_count=280
        ... )
        >>> print(f"数组长度: {len(values)}")  # 280
        >>> print(f"第一个值: {values[0]}")
        >>> print(f"最后一个值: {values[-1]}")
    """
    # 解析地址
    if isinstance(ecu_address, str):
        if ecu_address.startswith("0x") or ecu_address.startswith("0X"):
            address_int = int(ecu_address, 16)
        else:
            address_int = int(ecu_address)
    else:
        address_int = int(ecu_address)
    
    # 创建解码器
    try:
        decoder = RecordLayoutDecoder(record_layout)
    except KeyError as e:
        raise ValueError(f"不支持的记录布局: {record_layout}") from e
    
    # 验证元素个数
    if element_count <= 0:
        raise ValueError(f"元素个数必须大于 0，但得到: {element_count}")
    
    # 计算需要读取的总字节数
    total_bytes = decoder.element_size * element_count
    
    # 打开HEX文件并读取数据
    hex_file = hex_file_obj or IntelHexFile(hex_path)
    
    # 处理负数地址（转换为无符号地址用于HEX文件查找）
    if address_int < 0:
        unsigned_address = address_int & 0xFFFFFFFFFFFFFFFF
    else:
        unsigned_address = address_int
    
    # 从HEX文件读取指定地址的连续字节数据
    try:
        raw_bytes, line_no = hex_file.fetch_bytes(unsigned_address, total_bytes)
    except KeyError as e:
        raise KeyError(
            f"地址 0x{address_int:X} (长度 {total_bytes} 字节) 不在 HEX 数据的连续范围内"
        ) from e
    
    # 根据字节序调整解码器格式
    struct_format = decoder.struct_format
    if byte_order.lower() == "big":
        struct_format = struct_format.replace("<", ">")
        # 使用调整后的格式直接解析
        struct_obj = struct.Struct(struct_format)
        decoded_values = [
            struct_obj.unpack(raw_bytes[i : i + decoder.element_size])[0]
            for i in range(0, total_bytes, decoder.element_size)
        ]
    else:
        # 使用默认解码器（小端序）
        decoded_values = decoder.decode_many(raw_bytes, element_count)
    
    return decoded_values


def parse_hex_curve(
    *,
    a2l_id: int,
    hex_path: str,
    characteristic_name: str,
    characteristic_id: Optional[int] = None,
    hex_file_obj: Optional["IntelHexFile"] = None,
) -> Optional[Dict[str, object]]:
    """解析 CURVE 类型，返回 X/Y 坐标和值列表。
    
    Args:
        a2l_id: A2L 文件 ID
        hex_path: HEX 文件路径
        characteristic_name: 标定量名称
        characteristic_id: 标定量 ID（可选，如果提供则优先使用，避免同名不同地址的记录冲突）
        hex_file_obj: HEX 文件对象（可选，用于复用已打开的文件）
    """
    # 如果提供了 characteristic_id，优先使用 ID 查询（更准确）
    if characteristic_id:
        try:
            char_obj = Characteristic.objects.get(id=characteristic_id, a2l_file_id=a2l_id)
            characteristic = {
                "name": char_obj.name,
                "record_layout": char_obj.record_layout,
                "characteristic_type": char_obj.characteristic_type,
                "ecu_address": char_obj.ecu_address,
                "number": char_obj.number,
            }
        except Characteristic.DoesNotExist:
            logger.warning("CURVE 标定量不存在 (ID=%s): %s", characteristic_id, characteristic_name)
            return None
    else:
        # 否则按名称查询（向后兼容）
        characteristics = _fetch_characteristics_from_db(a2l_id, [characteristic_name])
        if not characteristics:
            logger.warning("CURVE 标定量不存在: %s", characteristic_name)
            return None
        characteristic = characteristics[0]

    if characteristic.get("characteristic_type") != "CURVE":
        logger.warning("标定量 %s 不是 CURVE 类型", characteristic_name)
        return None

    axis_definition = _fetch_curve_axis_definition(a2l_id, characteristic_name, characteristic_id)
    if not axis_definition:
        logger.warning("CURVE %s 未找到 X 轴定义", characteristic_name)
        return None

    y_layout = characteristic.get("record_layout")
    y_address = characteristic.get("ecu_address")
    if y_layout is None or y_address is None:
        logger.warning("CURVE %s 缺少 Y 轴记录布局或地址", characteristic_name)
        return None

    try:
        y_decoder = RecordLayoutDecoder(y_layout)
    except KeyError:
        logger.warning("CURVE %s 的 Y 轴记录布局暂不支持: %s", characteristic_name, y_layout)
        return None

    x_layout = axis_definition.get("record_layout")
    x_address = axis_definition.get("ecu_address")
    if x_layout is None or x_address is None:
        logger.warning("CURVE %s 的 X 轴记录布局或地址缺失", characteristic_name)
        return None

    try:
        x_decoder = RecordLayoutDecoder(x_layout)
    except KeyError:
        logger.warning("CURVE %s 的 X 轴记录布局暂不支持: %s", characteristic_name, x_layout)
        return None

    hex_file = hex_file_obj or IntelHexFile(hex_path)
    y_count = _normalize_characteristic_number(
        characteristic.get("number"), characteristic_type="CURVE"
    )
    if y_count <= 0:
        logger.warning("CURVE %s 的点数非法: %s", characteristic_name, y_count)
        return None
    x_count = axis_definition.get("max_axis_points") or y_count

    def _read_values(address: int, decoder: RecordLayoutDecoder, count: int) -> Tuple[List[float | int], int, bytes]:
        address_int = int(address)
        unsigned_address = address_int & 0xFFFFFFFFFFFFFFFF if address_int < 0 else address_int
        raw_bytes, line_no = hex_file.fetch_bytes(unsigned_address, decoder.element_size * count)
        values = decoder.decode_many(raw_bytes, count)
        return values, line_no, raw_bytes

    y_address_int = int(y_address)
    x_address_int = int(x_address)

    try:
        y_values, y_line, y_raw = _read_values(y_address_int, y_decoder, y_count)
    except KeyError:
        logger.warning("CURVE %s 的 Y 轴地址 0x%X 不在 HEX 中", characteristic_name, y_address_int)
        return None

    try:
        x_values, x_line, x_raw = _read_values(x_address_int, x_decoder, x_count)
    except KeyError:
        logger.warning("CURVE %s 的 X 轴地址 0x%X 不在 HEX 中", characteristic_name, x_address_int)
        return None

    point_count = min(len(x_values), len(y_values))
    if point_count == 0:
        logger.warning("CURVE %s 解析结果为空", characteristic_name)
        return None
    if len(x_values) != len(y_values):
        logger.warning(
            "CURVE %s 的 X/Y 数据长度不同: X=%d Y=%d，取前 %d 个匹配",
            characteristic_name,
            len(x_values),
            len(y_values),
            point_count,
        )
        x_values = x_values[:point_count]
        y_values = y_values[:point_count]

    x_address_hex, x_address_decimal = _format_address(x_address_int)
    y_address_hex, y_address_decimal = _format_address(y_address_int)

    return {
        "name": characteristic_name,
        "characteristic_type": "CURVE",
        "point_count": point_count,
        "x_axis": {
            "name": axis_definition.get("name"),
            "address": x_address_hex,
            "address_decimal": x_address_decimal,
            "record_layout": x_layout,
            "line_no": x_line,
            "byte_count": len(x_raw),
            "raw_bytes": x_raw.hex().upper(),
            "values": x_values,
        },
        "y_axis": {
            "name": characteristic.get("name"),
            "address": y_address_hex,
            "address_decimal": y_address_decimal,
            "record_layout": y_layout,
            "line_no": y_line,
            "byte_count": len(y_raw),
            "raw_bytes": y_raw.hex().upper(),
            "values": y_values,
        },
        "data_points": list(zip(x_values, y_values)),
    }


def parse_hex_map(
    *,
    a2l_id: int,
    hex_path: str,
    characteristic_name: str,
    characteristic_id: Optional[int] = None,
    hex_file_obj: Optional["IntelHexFile"] = None,
) -> Optional[Dict[str, object]]:
    """解析 MAP 类型，返回 X/Y 轴和二维矩阵数据。
    
    MAP 与 CURVE 类似，只是多了一个维度：
    - CURVE: 1维数据，zip(x, y) 形成数据点
    - MAP: 2维数据，z_values 按行存储为矩阵 [y][x]
    
    MAP 数据按行存储（Y 轴优先），即先存储第一行的所有 X 值，再存储第二行的所有 X 值，以此类推。
    数据矩阵大小为 x_count × y_count，存储顺序为：
    [y0_x0, y0_x1, ..., y0_xN, y1_x0, y1_x1, ..., y1_xN, ...]
    
    Args:
        a2l_id: A2L 文件 ID
        hex_path: HEX 文件路径
        characteristic_name: 标定量名称
        characteristic_id: 标定量 ID（可选，如果提供则优先使用，避免同名不同地址的记录冲突）
        hex_file_obj: HEX 文件对象（可选，用于复用已打开的文件）
    """
    # 如果提供了 characteristic_id，优先使用 ID 查询（更准确）
    if characteristic_id:
        try:
            char_obj = Characteristic.objects.get(id=characteristic_id, a2l_file_id=a2l_id)
            characteristic = {
                "name": char_obj.name,
                "record_layout": char_obj.record_layout,
                "characteristic_type": char_obj.characteristic_type,
                "ecu_address": char_obj.ecu_address,
                "number": char_obj.number,
            }
        except Characteristic.DoesNotExist:
            logger.warning("MAP 标定量不存在 (ID=%s): %s", characteristic_id, characteristic_name)
            return None
    else:
        # 否则按名称查询（向后兼容）
        characteristics = _fetch_characteristics_from_db(a2l_id, [characteristic_name])
        if not characteristics:
            logger.warning("MAP 标定量不存在: %s", characteristic_name)
            return None
        characteristic = characteristics[0]

    if characteristic.get("characteristic_type") != "MAP":
        logger.warning("标定量 %s 不是 MAP 类型", characteristic_name)
        return None

    # 获取 X 轴和 Y 轴定义（MAP 有两个轴，CURVE 只有一个 X 轴）
    axis_definitions = _fetch_map_axis_definitions(a2l_id, characteristic_name, characteristic_id)
    if not axis_definitions:
        logger.warning("MAP %s 未找到 X/Y 轴定义", characteristic_name)
        return None

    x_axis_def = axis_definitions.get("x_axis")
    y_axis_def = axis_definitions.get("y_axis")
    if not x_axis_def or not y_axis_def:
        logger.warning("MAP %s 的 X/Y 轴定义不完整", characteristic_name)
        return None

    # 复用 CURVE 的布局和地址验证逻辑
    z_layout = characteristic.get("record_layout")
    z_address = characteristic.get("ecu_address")
    if z_layout is None or z_address is None:
        logger.warning("MAP %s 缺少数据记录布局或地址", characteristic_name)
        return None

    x_layout = x_axis_def.get("record_layout")
    x_address = x_axis_def.get("ecu_address")
    if x_layout is None or x_address is None:
        logger.warning("MAP %s 的 X 轴记录布局或地址缺失", characteristic_name)
        return None

    y_layout = y_axis_def.get("record_layout")
    y_address = y_axis_def.get("ecu_address")
    if y_layout is None or y_address is None:
        logger.warning("MAP %s 的 Y 轴记录布局或地址缺失", characteristic_name)
        return None

    # 复用 CURVE 的解码器创建逻辑
    try:
        z_decoder = RecordLayoutDecoder(z_layout)
        x_decoder = RecordLayoutDecoder(x_layout)
        y_decoder = RecordLayoutDecoder(y_layout)
    except KeyError as e:
        logger.warning("MAP %s 的记录布局暂不支持: %s", characteristic_name, e)
        return None

    hex_file = hex_file_obj or IntelHexFile(hex_path)
    
    # 获取轴点数（类似 CURVE 的 number）
    x_count = x_axis_def.get("max_axis_points") or 0
    y_count = y_axis_def.get("max_axis_points") or 0
    if x_count <= 0 or y_count <= 0:
        logger.warning("MAP %s 的轴点数非法: X=%d Y=%d", characteristic_name, x_count, y_count)
        return None

    # MAP 数据总数 = X 轴点数 × Y 轴点数（这是与 CURVE 的主要区别）
    z_total_count = x_count * y_count

    # 复用 CURVE 的读取函数
    def _read_values(address: int, decoder: RecordLayoutDecoder, count: int) -> Tuple[List[float | int], int, bytes]:
        address_int = int(address)
        unsigned_address = address_int & 0xFFFFFFFFFFFFFFFF if address_int < 0 else address_int
        raw_bytes, line_no = hex_file.fetch_bytes(unsigned_address, decoder.element_size * count)
        values = decoder.decode_many(raw_bytes, count)
        return values, line_no, raw_bytes

    x_address_int = int(x_address)
    y_address_int = int(y_address)
    z_address_int = int(z_address)

    # 复用 CURVE 的读取逻辑（读取 X 和 Y 轴）
    try:
        x_values, x_line, x_raw = _read_values(x_address_int, x_decoder, x_count)
    except KeyError:
        logger.warning("MAP %s 的 X 轴地址 0x%X 不在 HEX 中", characteristic_name, x_address_int)
        return None

    try:
        y_values, y_line, y_raw = _read_values(y_address_int, y_decoder, y_count)
    except KeyError:
        logger.warning("MAP %s 的 Y 轴地址 0x%X 不在 HEX 中", characteristic_name, y_address_int)
        return None

    # MAP 特有的：读取 Z 数据（二维矩阵）
    try:
        z_values, z_line, z_raw = _read_values(z_address_int, z_decoder, z_total_count)
    except KeyError:
        logger.warning("MAP %s 的数据地址 0x%X 不在 HEX 中", characteristic_name, z_address_int)
        return None

    # MAP 特有的：将一维数组转换为二维矩阵（按行存储，Y 轴优先）
    # z_values 存储顺序: [y0_x0, y0_x1, ..., y0_xN, y1_x0, y1_x1, ..., y1_xN, ...]
    matrix = [z_values[y_idx * x_count:(y_idx + 1) * x_count] for y_idx in range(y_count)]

    # 复用 CURVE 的地址格式化逻辑
    x_address_hex, x_address_decimal = _format_address(x_address_int)
    y_address_hex, y_address_decimal = _format_address(y_address_int)
    z_address_hex, z_address_decimal = _format_address(z_address_int)

    # 复用 CURVE 的返回结构（只是增加了 z_data）
    return {
        "name": characteristic_name,
        "characteristic_type": "MAP",
        "x_count": x_count,
        "y_count": y_count,
        "x_axis": {
            "name": x_axis_def.get("name"),
            "address": x_address_hex,
            "address_decimal": x_address_decimal,
            "record_layout": x_layout,
            "line_no": x_line,
            "byte_count": len(x_raw),
            "raw_bytes": x_raw.hex().upper(),
            "values": x_values,
        },
        "y_axis": {
            "name": y_axis_def.get("name"),
            "address": y_address_hex,
            "address_decimal": y_address_decimal,
            "record_layout": y_layout,
            "line_no": y_line,
            "byte_count": len(y_raw),
            "raw_bytes": y_raw.hex().upper(),
            "values": y_values,
        },
        "z_data": {
            "address": z_address_hex,
            "address_decimal": z_address_decimal,
            "record_layout": z_layout,
            "line_no": z_line,
            "byte_count": len(z_raw),
            "raw_bytes": z_raw.hex().upper(),
            "matrix": matrix,  # 二维矩阵 [y][x]，这是与 CURVE 的主要区别
        },
    }


def parse_and_save_all_characteristics(
    *,
    hex_file: DataFile,
    a2l_id: int,
    hex_path: str,
    batch_size: int = 500,
) -> Dict[str, int]:
    """批量解析 HEX 文件中所有标定量并入库到 Hex 表。
    
    Args:
        hex_file: DataFile 实例，关联的 HEX 文件
        a2l_id: A2L 文件 ID
        hex_path: HEX 文件路径
        batch_size: 批量入库的批次大小，默认 500
    
    Returns:
        Dict[str, int]: 统计信息，包含各类型标定量的解析和入库数量
    """
    logger.info("开始批量解析 HEX 文件所有标定量: A2L ID=%s, HEX=%s", a2l_id, hex_path)
    
    stats = {
        "total_characteristics": 0,
        "parsed_count": 0,
        "saved_count": 0,
        "error_count": 0,
        "by_type": {
            "VALUE": {"parsed": 0, "saved": 0, "errors": 0},
            "VAL_BLK": {"parsed": 0, "saved": 0, "errors": 0},
            "CURVE": {"parsed": 0, "saved": 0, "errors": 0},
            "MAP": {"parsed": 0, "saved": 0, "errors": 0},
        }
    }
    
    # 查询该 A2L 文件的所有标定量
    characteristics = Characteristic.objects.filter(a2l_file_id=a2l_id).select_related('conversion_method')
    stats["total_characteristics"] = characteristics.count()
    
    if stats["total_characteristics"] == 0:
        # 获取 A2L 文件信息以便提供更详细的错误信息
        try:
            a2l_file = A2LFile.objects.get(id=a2l_id)
            a2l_file_name = a2l_file.name or f"A2L文件(id={a2l_id})"
        except A2LFile.DoesNotExist:
            a2l_file_name = f"A2L文件(id={a2l_id})"
        
        error_msg = f"关联的 A2L 文件 ({a2l_file_name}) 未解析标定量定义，请先解析 A2L 文件再解析 HEX 文件"
        logger.warning("A2L 文件 (id=%s) 未查询到标定量定义", a2l_id)
        raise ValueError(error_msg)
    
    logger.info("找到 %d 个标定量，开始逐个解析...", stats["total_characteristics"])
    
    # 获取或创建默认的 Maturity 对象（如果 Hex 模型要求 maturity 字段）
    default_maturity = None
    try:
        default_maturity = Maturity.objects.first()
        if not default_maturity:
            # 如果没有 Maturity 记录，创建一个默认的
            default_maturity = Maturity.objects.create(
                name="默认模板",
                value=0.0,
                description="系统自动创建的默认成熟度模板"
            )
            logger.info("创建默认 Maturity 对象: %s", default_maturity)
    except Exception as e:
        logger.warning("无法获取或创建默认 Maturity 对象: %s，将尝试设置为 None", str(e))
    
    hex_records_to_create: List[Hex] = []

    try:
        shared_hex_file = IntelHexFile(hex_path)
    except FileNotFoundError:
        logger.error("HEX 文件不存在: %s", hex_path)
        raise
    
    for characteristic in characteristics:
        char_name = characteristic.name
        char_type = characteristic.characteristic_type
        ecu_address = characteristic.ecu_address
        
        try:
            ecu_address_int = int(ecu_address)
        except (TypeError, ValueError):
            logger.info("标定量 %s 的 ecu_address 无效(%s)，跳过", char_name, ecu_address)
            continue

        # if ecu_address_int == 0:
        #     logger.info("标定量 %s 的 ecu_address 为 0，跳过入库", char_name)
        #     continue

        try:
            # 根据类型调用相应的解析函数
            if char_type == "VALUE":
                # VALUE 类型使用 parse_hex_characteristics
                results = parse_hex_characteristics(
                    a2l_id=a2l_id,
                    hex_path=hex_path,
                    characteristic_names=[char_name],
                    hex_file_obj=shared_hex_file,
                )
                if results:
                    result = results[0]
                    hex_record = Hex(
                        hex_file=hex_file,
                        characteristic=characteristic,
                        maturity=default_maturity,
                        line_no=result.get("line_no", 0),
                        byte_count=result.get("byte_count", 0),
                        offset_addr=0,  # VALUE 类型通常不需要 offset_addr
                        record_type=0,  # 数据记录类型
                        data_bytes=bytes.fromhex(result.get("raw_bytes", "")),
                        checksum=0,  # 校验和可以后续计算
                        current_value=[result.get("value")] if isinstance(result.get("value"), (int, float)) else result.get("value", []),
                    )
                    hex_records_to_create.append(hex_record)
                    stats["by_type"]["VALUE"]["parsed"] += 1
                    stats["parsed_count"] += 1
                else:
                    logger.warning("VALUE 标定量 %s (地址=0x%X) 解析失败，未返回结果", char_name, ecu_address_int)
                    stats["by_type"]["VALUE"]["errors"] += 1
                    stats["error_count"] += 1
                    
            elif char_type == "VAL_BLK":
                # VAL_BLK 类型
                element_count = characteristic.number or 0
                if element_count <= 0:
                    logger.warning("VAL_BLK %s 的 number 字段无效: %s", char_name, element_count)
                    stats["by_type"]["VAL_BLK"]["errors"] += 1
                    stats["error_count"] += 1
                    continue
                
                try:
                    values = parse_hex_val_blk(
                        hex_path=hex_path,
                        ecu_address=characteristic.ecu_address,
                        record_layout=characteristic.record_layout,
                        element_count=element_count,
                        hex_file_obj=shared_hex_file,
                    )
                    # VAL_BLK 需要从 HEX 文件获取 line_no 等信息
                    address_int = int(characteristic.ecu_address)
                    unsigned_address = address_int & 0xFFFFFFFFFFFFFFFF if address_int < 0 else address_int
                    decoder = RecordLayoutDecoder(characteristic.record_layout)
                    total_bytes = decoder.element_size * element_count
                    raw_bytes, line_no = shared_hex_file.fetch_bytes(unsigned_address, total_bytes)
                    
                    hex_record = Hex(
                        hex_file=hex_file,
                        characteristic=characteristic,
                        maturity=default_maturity,
                        line_no=line_no,
                        byte_count=len(raw_bytes),
                        offset_addr=0,
                        record_type=0,
                        data_bytes=raw_bytes,
                        checksum=0,
                        current_value=values,
                    )
                    hex_records_to_create.append(hex_record)
                    stats["by_type"]["VAL_BLK"]["parsed"] += 1
                    stats["parsed_count"] += 1
                except Exception as e:
                    logger.warning("解析 VAL_BLK %s 失败: %s", char_name, str(e))
                    stats["by_type"]["VAL_BLK"]["errors"] += 1
                    stats["error_count"] += 1
                    
            elif char_type == "CURVE":
                # CURVE 类型
                result = parse_hex_curve(
                    a2l_id=a2l_id,
                    hex_path=hex_path,
                    characteristic_name=char_name,
                    characteristic_id=characteristic.id,  # 传入 characteristic ID，避免同名不同地址的记录冲突
                    hex_file_obj=shared_hex_file,
                )
                if result:
                    # CURVE 类型需要存储 Y 轴数据（主数据）和 X 轴数据（可选）
                    y_axis = result.get("y_axis", {})
                    y_values = y_axis.get("values", [])
                    
                    # 存储 Y 轴数据（主数据）
                    hex_record = Hex(
                        hex_file=hex_file,
                        characteristic=characteristic,
                        maturity=default_maturity,
                        line_no=y_axis.get("line_no", 0),
                        byte_count=y_axis.get("byte_count", 0),
                        offset_addr=0,
                        record_type=0,
                        data_bytes=bytes.fromhex(y_axis.get("raw_bytes", "")),
                        checksum=0,
                        current_value=y_values,
                    )
                    hex_records_to_create.append(hex_record)
                    stats["by_type"]["CURVE"]["parsed"] += 1
                    stats["parsed_count"] += 1
                else:
                    logger.warning("CURVE 标定量 %s (ID=%s, 地址=0x%X) 解析失败，未返回结果。可能原因：X轴定义缺失、地址不在HEX中、记录布局不支持等", 
                                 char_name, characteristic.id, ecu_address_int)
                    stats["by_type"]["CURVE"]["errors"] += 1
                    stats["error_count"] += 1
                    
            elif char_type == "MAP":
                # MAP 类型
                result = parse_hex_map(
                    a2l_id=a2l_id,
                    hex_path=hex_path,
                    characteristic_name=char_name,
                    characteristic_id=characteristic.id,  # 传入 characteristic ID，避免同名不同地址的记录冲突
                    hex_file_obj=shared_hex_file,
                )
                if result:
                    # MAP 类型需要存储 Z 数据（二维矩阵），保持多维结构
                    z_data = result.get("z_data", {})
                    matrix = z_data.get("matrix", [])
                    
                    # 之前是展平为一维数组：flattened_values = [val for row in matrix for val in row]
                    # 现在直接存储二维矩阵，利用 JSONField 的嵌套列表特性支持多维数据
                    
                    hex_record = Hex(
                        hex_file=hex_file,
                        characteristic=characteristic,
                        maturity=default_maturity,
                        line_no=z_data.get("line_no", 0),
                        byte_count=z_data.get("byte_count", 0),
                        offset_addr=0,
                        record_type=0,
                        data_bytes=bytes.fromhex(z_data.get("raw_bytes", "")),
                        checksum=0,
                        current_value=matrix,  # 存储二维矩阵 [[row1], [row2], ...]
                    )
                    hex_records_to_create.append(hex_record)
                    stats["by_type"]["MAP"]["parsed"] += 1
                    stats["parsed_count"] += 1
                else:
                    logger.warning("MAP 标定量 %s (ID=%s, 地址=0x%X) 解析失败，未返回结果。可能原因：X/Y轴定义缺失、地址不在HEX中、记录布局不支持等", 
                                 char_name, characteristic.id, ecu_address_int)
                    stats["by_type"]["MAP"]["errors"] += 1
                    stats["error_count"] += 1
            else:
                logger.warning("不支持的标定量类型: %s (name=%s)", char_type, char_name)
                stats["error_count"] += 1
                
        except Exception as e:
            logger.exception("解析标定量 %s 时发生异常: %s", char_name, str(e))
            stats["error_count"] += 1
            if char_type in stats["by_type"]:
                stats["by_type"][char_type]["errors"] += 1
    
    # 批量入库
    if hex_records_to_create:
        try:
            with transaction.atomic():
                # 使用 bulk_create 批量插入，ignore_conflicts=True 避免重复数据冲突
                Hex.objects.bulk_create(hex_records_to_create, batch_size=batch_size, ignore_conflicts=True)
                stats["saved_count"] = len(hex_records_to_create)
                for record in hex_records_to_create:
                    char_type = record.characteristic.characteristic_type
                    if char_type in stats["by_type"]:
                        stats["by_type"][char_type]["saved"] += 1
                logger.info("成功入库 %d 条 HEX 数据记录", stats["saved_count"])
        except Exception as e:
            logger.exception("批量入库 HEX 数据失败: %s", str(e))
            raise
    
    logger.info("批量解析完成: 总计=%d, 解析成功=%d, 入库=%d, 错误=%d", 
                stats["total_characteristics"], stats["parsed_count"], 
                stats["saved_count"], stats["error_count"])
    
    return stats


__all__ = [
    "HexParseError",
    "HexRecord",
    "IntelHexFile",
    "RecordLayoutDecoder",
    "get_characteristic_address",
    "parse_hex_characteristics",
    "parse_hex_address_to_value",
    "parse_hex_val_blk",
    "parse_hex_curve",
    "parse_hex_map",
    "parse_and_save_all_characteristics",
]



if __name__ == "__main__":
    import os
    import sys
    import django
    
    # 设置 Django 环境
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'calibracloud_webapi.settings.dev')
    
    # 添加项目根目录到 Python 路径
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    
    # 初始化 Django
    django.setup()
    
    # 共享配置
    a2l_id = 12
    hex_path = os.path.join(r"D:\log\calibracloud\uploads\OTHER\2025\10", "1760076706451.hex")
    
    # 测试1: 查询标定量地址信息
    print("=" * 80)
    print("测试1: 查询标定量地址信息")
    print("=" * 80)
    # characteristic_name = "CtrlACC_mV_11kwTurnU_C"
    characteristic_name = "Glb_SuperEV_D"
    address_info = get_characteristic_address(a2l_id=a2l_id, characteristic_name=characteristic_name)
    if address_info:
        print(f"名称: {address_info.get('name')}")
        print(f"ECU地址(十进制): {address_info.get('address_decimal')}")
        print(f"ECU地址(十六进制): {address_info.get('address_hex')}")
        print(f"记录布局: {address_info.get('record_layout')}")
        print(f"标定量类型: {address_info.get('characteristic_type')}")
        print(f"数量: {address_info.get('number')}")
        
        # 测试2: 根据地址解析物理值
        print("\n" + "=" * 80)
        print("测试2: 根据ECU地址解析物理值")
        print("=" * 80)
        ecu_address = address_info.get('address_hex')  # 例如: "0x40314"
        
        if os.path.exists(hex_path):
            try:
                # value = parse_hex_address_to_value(hex_path, ecu_address, data_type="FLOAT32")
                value = parse_hex_address_to_value(hex_path, ecu_address, data_type="Scalar_UBYTE")
                print(f"ECU地址: {ecu_address}")
                print(f"物理值: {value}")
            except Exception as e:
                print(f"解析失败: {e}")
        else:
            print(f"HEX文件不存在: {hex_path}")
    else:
        print(f"❌ 未找到标定量: {characteristic_name}")
    
    # 测试3: 解析 VAL_BLK 类型（连续数组）
    print("\n" + "=" * 80)
    print("测试3: 解析 VAL_BLK 类型（连续数组）")
    print("=" * 80)
    val_blk_name = "MsrModlT_num_Layout_2"  # 示例 VAL_BLK 标定量
    val_blk_info = get_characteristic_address(a2l_id=a2l_id, characteristic_name=val_blk_name)
    if val_blk_info:
        print(f"名称: {val_blk_info.get('name')}")
        print(f"类型: {val_blk_info.get('characteristic_type')}")
        print(f"ECU地址: {val_blk_info.get('address_hex')}")
        print(f"记录布局: {val_blk_info.get('record_layout')}")
        print(f"数组元素个数: {val_blk_info.get('number')}")
        
        if os.path.exists(hex_path):
            try:
                # 方法1: 使用 parse_hex_characteristics（推荐）
                print("\n--- 方法1: 使用 parse_hex_characteristics ---")
                results = parse_hex_characteristics(
                    a2l_id=a2l_id,
                    hex_path=hex_path,
                    characteristic_names=[val_blk_name]
                )
                if results:
                    char = results[0]
                    values = char['value']
                    print(f"数组长度: {len(values)}")
                    print(f"前5个值: {values[:5]}")
                    print(f"后5个值: {values[-5:]}")
                    print(f"Stored Value (JSON Structure): {values}")
                
                # 方法2: 使用 parse_hex_val_blk（直接指定参数）
                print("\n--- 方法2: 使用 parse_hex_val_blk ---")
                values = parse_hex_val_blk(
                    hex_path=hex_path,
                    ecu_address=val_blk_info.get('address_hex'),
                    record_layout=val_blk_info.get('record_layout'),
                    element_count=val_blk_info.get('number')
                )
                print(f"数组长度: {len(values)}")
                print(f"前5个值: {values[:5]}")
                print(f"后5个值: {values[-5:]}")
                print(f"Stored Value (JSON Structure): {values}")
            except Exception as e:
                print(f"解析失败: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"HEX文件不存在: {hex_path}")
    else:
        print(f"❌ 未找到 VAL_BLK 标定量: {val_blk_name}")

    # 测试4: 解析 CURVE 类型（打印坐标和值）
    print("\n" + "=" * 80)
    print("测试4: 解析 CURVE 类型（打印坐标和值）")
    print("=" * 80)
    curve_name = "MsrOutT_degC_ResToT_T"
    if os.path.exists(hex_path):
        try:
            curve_result = parse_hex_curve(
                a2l_id=a2l_id,
                hex_path=hex_path,
                characteristic_name=curve_name,
            )
            if not curve_result:
                print(f"❌ 未找到 CURVE 标定量或解析失败: {curve_name}")
            else:
                print(
                    f"名称: {curve_result['name']}, "
                    f"数据点数量: {curve_result['point_count']}"
                )
                print("X/Y 坐标及对应值:")
                for idx, (x_val, y_val) in enumerate(curve_result["data_points"]):
                    print(f"  [{idx}] X={x_val}, Y={y_val}")
                print(f"\nStored Value (JSON Structure): {curve_result['y_axis']['values']}")
        except Exception as exc:
            print(f"解析 CURVE 失败: {curve_name}, 错误: {exc}")
            import traceback
            traceback.print_exc()
    else:
        print(f"HEX文件不存在: {hex_path}")

    # 测试5: 解析 MAP 类型（打印 X/Y 轴和二维矩阵）
    print("\n" + "=" * 80)
    print("测试5: 解析 MAP 类型（打印 X/Y 轴和二维矩阵）")
    print("=" * 80)
    map_name = "EstCPC_rate_ChrgMapI_T"
    if os.path.exists(hex_path):
        try:
            map_result = parse_hex_map(
                a2l_id=a2l_id,
                hex_path=hex_path,
                characteristic_name=map_name,
            )
            if not map_result:
                print(f"❌ 未找到 MAP 标定量或解析失败: {map_name}")
            else:
                print(f"名称: {map_result['name']}")
                print(f"X 轴点数: {map_result['x_count']}, Y 轴点数: {map_result['y_count']}")
                print(f"X 轴名称: {map_result['x_axis']['name']}")
                print(f"Y 轴名称: {map_result['y_axis']['name']}")
                print(f"X 轴地址: {map_result['x_axis']['address']}")
                print(f"Y 轴地址: {map_result['y_axis']['address']}")
                print(f"数据地址: {map_result['z_data']['address']}")
                
                print("\nX 轴值:")
                x_values = map_result['x_axis']['values']
                for idx, val in enumerate(x_values):
                    print(f"  X[{idx}] = {val}")
                
                print("\nY 轴值:")
                y_values = map_result['y_axis']['values']
                for idx, val in enumerate(y_values):
                    print(f"  Y[{idx}] = {val}")
                
                print("\n二维矩阵数据 (matrix[y][x]):")
                matrix = map_result['z_data']['matrix']
                for y_idx, row in enumerate(matrix):
                    print(f"  Y[{y_idx}] = {row}")
                
                print(f"\nStored Value (JSON Structure): {matrix}")
        except Exception as exc:
            print(f"解析 MAP 失败: {map_name}, 错误: {exc}")
            import traceback
            traceback.print_exc()
    else:
        print(f"HEX文件不存在: {hex_path}")