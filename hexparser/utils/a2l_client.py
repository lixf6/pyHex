from __future__ import annotations

import logging
import os
from collections import Counter
from contextlib import contextmanager
from typing import Dict, List
import pya2l.model as model
from pya2l import DB

def _get_a2l_cache_files(file_path: str) -> List[str]:
    """Collect pya2l cache files generated next to the original A2L file."""
    directory = os.path.dirname(file_path)
    candidates = [
        f"{file_path}.a2ldb",
        f"{file_path}.tmp",
        os.path.join(directory, "A2L.tmp"),
        os.path.join(directory, "AML.tmp"),
        os.path.join(directory, "IFDATA.tmp"),
    ]
    return [candidate for candidate in candidates if os.path.exists(candidate)]


@contextmanager
def _a2l_session(file_path: str, cleanup_cache: bool = False):
    """Provide a pya2l session for the given file and optionally cleanup cache files."""
    file_path = os.path.abspath(os.path.normpath(file_path))
    if not os.path.exists(file_path):
        raise RuntimeError(f"文件不存在: {file_path}")
    if not os.path.isfile(file_path):
        raise RuntimeError(f"路径不是文件: {file_path}")

    db = DB()
    session = None
    try:
        try:
            session = db.open_existing(file_path)
            logging.info("复用已有 A2L 数据库: %s", f"{file_path}")
        except Exception:
            db.import_a2l(file_path)
            session = db.open_existing(file_path)
            logging.info("首次导入 A2L 文件并生成缓存: %s", file_path)
        yield session
    finally:
        if session:
            session.close()
        if cleanup_cache:
            for cache_path in _get_a2l_cache_files(file_path):
                try:
                    os.remove(cache_path)
                except Exception:
                    pass

def _decode_text(text: object) -> str:
    """Attempt to fix garbled strings that were interpreted as latin1."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return ""
    if any(ord(ch) > 127 for ch in text):
        try:
            return text.encode("latin1").decode("gbk")
        except UnicodeError:
            return text
    return text


def _ensure_list(value) -> List:
    """Normalize ORM relationship results (single object or iterable) into a list."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def parse_all_a2l_data(file_path: str, cleanup_cache: bool = False) -> Dict:
    """Parse primary A2L structures and return a dictionary of primary entities."""

    logging.info("开始解析 A2L 文件: %s", file_path)

    result: Dict[str, object] = {
        "project": None,
        "module": None,
        "module_parameters": None,
        "asap2_version": None,
        "compu_methods": [],
        "characteristics": [],
        "measurements": [],
        "axis_pts": [],
        "axis_descrs": [],
        "axis_pts_refs": [],
        "axis_pts_x": [],
        "record_layouts": [],
    }

    with _a2l_session(file_path, cleanup_cache=cleanup_cache) as session:
        # 读取 PROJECT 信息（仅取第一个）
        project = session.query(model.Project).first()
        if project:
            result["project"] = {
                "name": _decode_text(getattr(project, "name", "")),
                "long_identifier": _decode_text(getattr(project, "longIdentifier", "")),
            }

        # 读取 MODULE 信息（仅取第一个）
        module = session.query(model.Module).first()
        if module:
            project_obj = getattr(module, "project", None)
            result["module"] = {
                "name": _decode_text(getattr(module, "name", "")),
                "long_identifier": _decode_text(getattr(module, "longIdentifier", "")),
                "project_name": _decode_text(getattr(project_obj, "name", None)),
            }

        # # 读取 MODULE_PARAMETER 信息（仅取第一个）
        # module_parameter = session.query(model.ModuleParameter).first()
        # if module_parameter:
        #     module_obj = getattr(module_parameter, "module", None)
        #     result["module_parameters"] = {
        #         "module_name": getattr(module_obj, "name", ""),
        #         "version_identifier": getattr(module_parameter, "version_identifier", ""),
        #         "supplier": getattr(module_parameter, "supplier", ""),
        #         "customer": getattr(module_parameter, "customer", ""),
        #         "customer_no": getattr(module_parameter, "customer_no", ""),
        #         "user": getattr(module_parameter, "user", ""),
        #         "phone_no": getattr(module_parameter, "phone_no", ""),
        #         "ecu": getattr(module_parameter, "ecu", ""),
        #         "cpu_type": getattr(module_parameter, "cpu_type", ""),
        #         "no_of_interfaces": getattr(module_parameter, "no_of_interfaces", 1) or 1,
        #     }

        # 读取 ASAP2_VERSION 信息
        version = session.query(model.Asap2Version).first()
        if version:
            result["asap2_version"] = {
                "version_no": getattr(version, "versionNo", 1),
                "upgrade_no": getattr(version, "upgradeNo", 0),
            }

        # 读取 COMPU_METHOD 信息
        compu_methods = []
        for compu_method in session.query(model.CompuMethod).all():
            compu_methods.append({
                "name": _decode_text(getattr(compu_method, "name", "")),
                "long_identifier": _decode_text(getattr(compu_method, "longIdentifier", "")),
                "conversion_type": getattr(compu_method, "conversionType", ""),
                "format_str": getattr(compu_method, "format", ""),
                # "units": getattr(compu_method, "units", ""),
                # "coeffs": coeffs,
            })
        result["compu_methods"] = compu_methods

        # 读取 CHARACTERISTIC 信息（先于 Measurement）
        characteristics = []
        axis_descr_records: List[Dict[str, object]] = []
        axis_pts_ref_records: List[Dict[str, object]] = []
        axis_supported_types = {"CURVE", "MAP", "CUBOID"}

        for characteristic in session.query(model.Characteristic).all():
            char_name = _decode_text(getattr(characteristic, "name"))
            long_identifier = _decode_text(getattr(characteristic, "longIdentifier", ""))
            char_type = getattr(characteristic, "type")
            number = getattr(characteristic, "number", 0) or 0
            axis_names: List[str] = []

            axis_descrs: List = []

            # 对于需要坐标轴的类型（CURVE/MAP/CUBOID），解析 AXIS_DESCR / AXIS_PTS 信息
            if char_type in axis_supported_types:
                axis_descrs = _ensure_list(getattr(characteristic, "axis_descrs", []))

                char_id = getattr(characteristic, "id", None) or getattr(characteristic, "rid", None)
                # pya2l 有时只返回第一个 AxisDescr，这里额外查询并合并
                if char_id and hasattr(model.AxisDescr, "_characteristic_rid"):
                    extra_axis_descrs = list(
                        session.query(model.AxisDescr)
                        .filter(model.AxisDescr._characteristic_rid == char_id)
                        .all()
                    )
                    if extra_axis_descrs:
                        existing_ids = {
                            getattr(axis_descr, "id", None)
                            or getattr(axis_descr, "rid", None)
                            for axis_descr in axis_descrs
                        }
                        for axis_descr in extra_axis_descrs:
                            axis_id = getattr(axis_descr, "id", None) or getattr(axis_descr, "rid", None)
                            if axis_id not in existing_ids:
                                axis_descrs.append(axis_descr)
                                existing_ids.add(axis_id)

                if not axis_descrs and char_id:
                    axis_descrs = list(
                        session.query(model.AxisDescr)
                        .filter(model.AxisDescr._characteristic_rid == char_id)
                        .all()
                    )

                if axis_descrs and char_type == "CURVE":
                    axis_descr = axis_descrs[0]
                    number = (
                        getattr(axis_descr, "maxAxisPoints", None)
                        or getattr(axis_descr, "numberOfAxisPts", None)
                        or getattr(axis_descr, "number", None)
                        or number
                    )

                    if not number:
                        axis_pts_refs = _ensure_list(getattr(axis_descr, "axis_pts_ref", []))
                        if not axis_pts_refs:
                            axis_descr_id = getattr(axis_descr, "id", None) or getattr(axis_descr, "rid", None)
                            if axis_descr_id and hasattr(model.AxisPtsRef, "_axis_descr_rid"):
                                axis_pts_refs = list(
                                    session.query(model.AxisPtsRef)
                                    .filter(model.AxisPtsRef._axis_descr_rid == axis_descr_id)
                                    .all()
                                )

                        if axis_pts_refs:
                            axis_pts_ref = axis_pts_refs[0]
                            axis_pts_name = (
                                getattr(axis_pts_ref, "axisPoints", None)
                                or getattr(axis_pts_ref, "axis_pts", None)
                                or getattr(axis_pts_ref, "name", None)
                            )
                            if axis_pts_name:
                                axis_pts = session.query(model.AxisPts).filter_by(name=axis_pts_name).first()
                                if axis_pts:
                                    number = (
                                        getattr(axis_pts, "maxAxisPoints", None)
                                        or getattr(axis_pts, "max_axis_points", None)
                                        or getattr(axis_pts, "number", None)
                                        or number
                                    )

                axis_descrs_list = _ensure_list(axis_descrs)
                for axis_descr in axis_descrs_list:
                    axis_pts_refs = _ensure_list(getattr(axis_descr, "axis_pts_ref", []))
                    if not axis_pts_refs:
                        axis_descr_id = getattr(axis_descr, "id", None) or getattr(axis_descr, "rid", None)
                        if axis_descr_id and hasattr(model.AxisPtsRef, "_axis_descr_rid"):
                            axis_pts_refs = list(
                                session.query(model.AxisPtsRef)
                                .filter(model.AxisPtsRef._axis_descr_rid == axis_descr_id)
                                .all()
                            )
                    axis_pts_ref_names_local: List[str] = []
                    for axis_pts_ref in axis_pts_refs:
                        axis_pts_name = (
                            getattr(axis_pts_ref, "axisPoints", None)
                            or getattr(axis_pts_ref, "axis_pts", None)
                            or getattr(axis_pts_ref, "name", None)
                        )
                        if axis_pts_name:
                            axis_pts_ref_names_local.append(axis_pts_name)
                            axis_names.append(axis_pts_name)
                            axis_pts_ref_records.append({
                                "characteristic_name": char_name,
                                "attribute": getattr(axis_descr, "attribute", ""),
                                "conversion_method": getattr(axis_descr, "conversion", ""),
                                "axis_points": axis_pts_name,
                            })
                    axis_descr_data = {
                        "characteristic_name": char_name,
                        "attribute": getattr(axis_descr, "attribute", ""),
                        "input_quantity": getattr(axis_descr, "inputQuantity", ""),
                        "conversion_method": getattr(axis_descr, "conversion", ""),
                        "max_axis_points": (
                            getattr(axis_descr, "maxAxisPoints", None)
                            or getattr(axis_descr, "numberOfAxisPts", None)
                            or getattr(axis_descr, "number", None)
                            or 0
                        ),
                        "lower_limit": getattr(axis_descr, "lowerLimit", 0.0) or 0.0,
                        "upper_limit": getattr(axis_descr, "upperLimit", 0.0) or 0.0,
                        "axis_pts_ref_name": axis_pts_ref_names_local[0] if axis_pts_ref_names_local else "",
                    }
                    axis_descr_records.append(axis_descr_data)

            characteristics.append({
                "name": char_name,
                "long_identifier": long_identifier,
                "characteristic_type": char_type,
                "ecu_address": getattr(characteristic, "address"),
                "record_layout": getattr(characteristic, "deposit"),
                "conversion_method": getattr(characteristic, "conversion"),
                "max_diff": getattr(characteristic, "maxDiff"),
                "lower_limit": getattr(characteristic, "lowerLimit"),
                "upper_limit": getattr(characteristic, "upperLimit"),
                "number": number,
                "axis_pts_refs": axis_names,
            })
        result["characteristics"] = characteristics
        result["axis_descrs"] = axis_descr_records
        result["axis_pts_refs"] = axis_pts_ref_records

        # 读取 MEASUREMENT 信息
        measurements = []
        for measurement in session.query(model.Measurement).all():
            meas_address = (
                getattr(measurement, "address", None)
                or getattr(measurement, "ecuAddress", None)
                or getattr(measurement, "ecu_address", None)
                or 0
            )
            measurements.append({
                "name": _decode_text(getattr(measurement, "name")),
                "long_identifier": _decode_text(getattr(measurement, "longIdentifier", "")),
                "datatype": getattr(measurement, "datatype"),
                "conversion_method": getattr(measurement, "conversion"),
                "resolution": getattr(measurement, "resolution"),
                "accuracy": getattr(measurement, "accuracy"),
                "lower_limit": getattr(measurement, "lowerLimit"),
                "upper_limit": getattr(measurement, "upperLimit"),
                "ecu_address": meas_address,
            })
        result["measurements"] = measurements

        # 读取 AXIS_PTS 信息
        axis_pts_records = []
        for axis_pts in session.query(model.AxisPts).all():
            axis_address = (
                getattr(axis_pts, "address", None)
                or getattr(axis_pts, "ecuAddress", None)
                or getattr(axis_pts, "ecu_address", None)
                or 0
            )
            axis_pts_records.append({
                "name": _decode_text(getattr(axis_pts, "name")),
                "long_identifier": _decode_text(getattr(axis_pts, "longIdentifier", "")),
                "ecu_address": axis_address,
                "input_quantity": getattr(axis_pts, "inputQuantity"),
                "record_layout": getattr(axis_pts, "depositAttr"),
                "max_diff": getattr(axis_pts, "maxDiff"),
                "conversion_method": getattr(axis_pts, "conversion"),
                "max_axis_points": getattr(axis_pts, "maxAxisPoints"),
                "lower_limit": getattr(axis_pts, "lowerLimit"),
                "upper_limit": getattr(axis_pts, "upperLimit"),
            })
        result["axis_pts"] = axis_pts_records

        # 读取 AXIS_PTS_X 信息
        axis_pts_x_records = []
        for axis_pts_x in session.query(model.AxisPtsX).all():
            record_layout = getattr(axis_pts_x, "record_layout", None)
            axis_pts_x_records.append({
                "record_layout_name": getattr(record_layout, "name", None),
                "position": getattr(axis_pts_x, "position", 0),
                "datatype": getattr(axis_pts_x, "datatype", ""),
                "index_incr": getattr(axis_pts_x, "indexIncr", ""),
                "addressing": getattr(axis_pts_x, "addressing", ""),
            })
        result["axis_pts_x"] = axis_pts_x_records

        # 读取 RECORD_LAYOUT 信息
        record_layouts = []
        for module_obj in session.query(model.Module).all():
            layouts = getattr(module_obj, "record_layouts", [])
            if not layouts:
                layouts = session.query(model.RecordLayout).filter_by(module=module_obj).all()
            for layout in layouts:
                record_layouts.append({
                "module_name": _decode_text(getattr(module_obj, "name", "")),
                "name": _decode_text(getattr(layout, "name", "")),
                })
        result["record_layouts"] = record_layouts

    return result


if __name__ == "__main__":  # pragma: no cover
    SAMPLE_PATH = os.path.join(r"D:\log\calibracloud\uploads\A2L\2025\10", "1760076474350.a2l")
    with _a2l_session(SAMPLE_PATH) as SESSION:
        print(SESSION)
    result = parse_all_a2l_data(SAMPLE_PATH)
    # 打印 result 的 keys
    print('project: ', result['project'])
    print('module: ', result['module'])
    print('asap2_version: ', result['asap2_version'])

    print('compu_methods: ', len(result['compu_methods']))
    print('characteristics: ', len(result['characteristics']))
    print('measurements: ', len(result['measurements']))
    print('axis_pts: ', len(result['axis_pts']))
    print('record_layouts: ', len(result['record_layouts']))

    # 仅打印 CURVE 类型的 name 和 number，方便调试
    # curve_chars = [
    #     characteristic
    #     for characteristic in result['characteristics']
    #     if characteristic.get('characteristic_type') == 'CURVE'
    # ]
    # print(f"CURVE characteristics: {curve_chars}")

    # print("CURVE characteristics (name -> number, ecu_address):")
    # for characteristic in curve_chars:
    #     print(
    #         f"  {_decode_text(characteristic.get('name'))}: "
    #         # f"  {_decode_text(characteristic.get('long_identifier'))}: "
    #         f"{characteristic.get('number')} "
    #         f"(ecu_address={characteristic.get('ecu_address')})"
    #     )

    # zero_address = [
    #     characteristic
    #     for characteristic in result['characteristics']
    #     if (characteristic.get('ecu_address') or 0) == 0
    # ]
    # non_zero_address = len(result['characteristics']) - len(zero_address)
    # print(f"characteristics with ecu_address == 0: {len(zero_address)}")
    # print(f"characteristics with ecu_address != 0: {non_zero_address}")

    type_counter = Counter(
        characteristic.get('characteristic_type', 'UNKNOWN')
        for characteristic in result['characteristics']
    )
    print("Characteristic type counts:")
    for type_name, count in sorted(type_counter.items()):
        print(f"  {type_name}: {count}")

    print("\nAXIS_PTS sample (DCC_A_ByGBChrgIRiseRng):")
    axis_sample = next(
        (
            axis
            for axis in result['axis_pts']
            if axis.get("name") == "DCC_A_ByGBChrgIRiseRng"
        ),
        None,
    )
    # if axis_sample:
    #     print(
    #         f"  name: {axis_sample.get('name')}, "
    #         f"address: {axis_sample.get('ecu_address')}, "
    #         f"max_axis_points: {axis_sample.get('max_axis_points')}"
    #     )
    # else:
    #     print("  AxisPts 'DCC_A_ByGBChrgIRiseRng' not found.")

    # print('axis_pts: ', result['axis_pts'])
