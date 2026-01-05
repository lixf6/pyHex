import logging
from typing import Dict, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor

from django.db import transaction
from django.db import models

from hexparser.models import (
    A2LFile,
    A2LProject,
    A2LModule,
    Asap2Version,
    CompuMethod,
    Coeffs,
    Measurement,
    Characteristic,
    AxisPts,
    AxisDescr,
    AxisPtsRef,
    AxisPtsX,
    RecordLayout,
    WorkPackage,
)


class A2LDataImporter:
    """Encapsulate logic for persisting parsed A2L data into database tables."""

    def _safe_get_field_value(self, instance, field_name):
        """安全地获取字段值，对于 ForeignKey 字段使用 field_id 避免访问不存在的关联对象。"""
        try:
            field = instance._meta.get_field(field_name)
            if isinstance(field, models.ForeignKey):
                # 对于 ForeignKey，使用 field_id 来获取值，避免触发数据库查询
                field_id_name = f"{field_name}_id"
                return getattr(instance, field_id_name, None)
            else:
                return getattr(instance, field_name)
        except Exception as e:
            logging.warning("获取字段值失败: instance=%s, field=%s, error=%s", instance, field_name, e)
            # 如果获取失败，尝试直接获取
            try:
                return getattr(instance, field_name)
            except Exception:
                return None

    def _safe_set_field_value(self, instance, field_name, value):
        """安全地设置字段值，对于 ForeignKey 字段使用 field_id 来设置，避免需要对象实例。"""
        try:
            field = instance._meta.get_field(field_name)
            if isinstance(field, models.ForeignKey):
                # 对于 ForeignKey，使用 field_id 来设置值
                field_id_name = f"{field_name}_id"
                # 如果 value 是对象，获取其 ID；如果已经是 ID，直接使用
                if hasattr(value, 'id'):
                    value = value.id
                setattr(instance, field_id_name, value)
            else:
                setattr(instance, field_name, value)
        except Exception as e:
            logging.warning("设置字段值失败: instance=%s, field=%s, value=%s, error=%s", instance, field_name, value, e)
            # 如果设置失败，尝试直接设置
            setattr(instance, field_name, value)

    def __init__(self, a2l_file: A2LFile, module_id: Optional[str] = None, updater: str = "system") -> None:
        self.a2l_file = a2l_file
        self.module_id = module_id
        self.updater = updater or "system"
        self.default_module = self._resolve_default_module()
        # 使用默认的"未绑定"工作包，后续可手工绑定
        self.default_work_package = self._resolve_default_unbound_work_package()

    def save(self, parsed_data: Dict) -> Dict[str, int]:
        """Persist parsed A2L data to the database.

        Returns statistics containing the number of newly created records per model.
        """
        logging.info("开始入库 A2L 数据: %s", len(parsed_data))
        stats = {
            "projects": 0,
            "modules": 0,
            "module_parameters": 0,
            "asap2_version": 0,
            "compu_methods": 0,
            "coeffs": 0,
            "measurements": 0,
            "characteristics": 0,
            "axis_pts": 0,
            "axis_descrs": 0,
            "axis_pts_refs": 0,
            "axis_pts_x": 0,
            "record_layouts": 0,
        }

        # 原子操作，要么全部成功，要么全部失败
        with transaction.atomic():
            logging.info("原子操作>>>开始入库 A2L 数据: %s", self.a2l_file.id)
            # 串行执行依赖项
            project = self._sync_project(parsed_data, stats)
            module = self._sync_module(parsed_data, project, stats)
            if module and not self.default_module:
                self.default_module = module
            # self._sync_module_parameters(parsed_data, module, stats)
            self._sync_asap_version(parsed_data, stats)
            compu_methods, old_compu_method_id_to_name = self._sync_compu_methods(parsed_data, stats)
            compu_method_map = self._build_compu_method_map(compu_methods)
            
            # 先删除依赖项（AxisPtsRef 和 AxisDescr），因为它们依赖 Characteristic
            # 注意：必须在更新 Characteristic 之前删除，否则会违反外键约束
            characteristics_ids = Characteristic.objects.filter(
                a2l_file=self.a2l_file
            ).values_list('id', flat=True)
            if characteristics_ids:
                # 先删除依赖项
                axis_descrs_ids = AxisDescr.objects.filter(
                    characteristic_id__in=characteristics_ids
                ).values_list('id', flat=True)
                if axis_descrs_ids:
                    axis_pts_ref_deleted = AxisPtsRef.objects.filter(
                        axis_descr_id__in=axis_descrs_ids
                    ).delete()[0]
                    if axis_pts_ref_deleted > 0:
                        logging.info("删除该 A2L 文件相关的旧 AxisPtsRef 记录: %d 条 (A2L文件ID: %s)", axis_pts_ref_deleted, self.a2l_file.id)
                axis_descr_deleted = AxisDescr.objects.filter(
                    characteristic_id__in=characteristics_ids
                ).delete()[0]
                if axis_descr_deleted > 0:
                    logging.info("删除该 A2L 文件相关的旧 AxisDescr 记录: %d 条 (A2L文件ID: %s)", axis_descr_deleted, self.a2l_file.id)
            
            # 查询现有记录，用于判重（基于 unique_together: a2l_file, name, ecu_address, conversion_method）
            # 注意：使用 conversion_method.name 而不是 conversion_method_id，因为 CompuMethod 采用"先删除后创建"策略，ID 会变化
            # 由于 CompuMethod 已被删除，我们需要通过 conversion_method_id 从映射中获取名称
            existing_char_dict = {}
            existing_char_count = Characteristic.objects.filter(a2l_file=self.a2l_file).count()
            for item in Characteristic.objects.filter(a2l_file=self.a2l_file):
                # 通过 conversion_method_id 从映射中获取名称，如果映射中没有则尝试查询（可能已被删除）
                conversion_method_name = ""
                if item.conversion_method_id:
                    conversion_method_name = old_compu_method_id_to_name.get(item.conversion_method_id, "")
                    # 如果映射中没有，尝试直接查询（可能 CompuMethod 还存在，但不在当前 A2L 文件中）
                    if not conversion_method_name:
                        try:
                            cm = CompuMethod.objects.get(id=item.conversion_method_id)
                            conversion_method_name = cm.name
                        except CompuMethod.DoesNotExist:
                            conversion_method_name = ""
                key = (item.name, item.ecu_address, conversion_method_name)
                existing_char_dict[key] = item
            logging.info("查询到现有 Characteristic 记录: %d 条 (A2L文件ID: %s)", existing_char_count, self.a2l_file.id)
            
            existing_meas_dict = {}
            existing_meas_count = Measurement.objects.filter(a2l_file=self.a2l_file).count()
            for item in Measurement.objects.filter(a2l_file=self.a2l_file):
                # 通过 conversion_method_id 从映射中获取名称，如果映射中没有则尝试查询（可能已被删除）
                conversion_method_name = ""
                if item.conversion_method_id:
                    conversion_method_name = old_compu_method_id_to_name.get(item.conversion_method_id, "")
                    # 如果映射中没有，尝试直接查询（可能 CompuMethod 还存在，但不在当前 A2L 文件中）
                    if not conversion_method_name:
                        try:
                            cm = CompuMethod.objects.get(id=item.conversion_method_id)
                            conversion_method_name = cm.name
                        except CompuMethod.DoesNotExist:
                            conversion_method_name = ""
                key = (item.name, item.ecu_address, conversion_method_name)
                existing_meas_dict[key] = item
            logging.info("查询到现有 Measurement 记录: %d 条 (A2L文件ID: %s)", existing_meas_count, self.a2l_file.id)
            
            # 并行准备大数据量的数据，然后串行批量插入
            # 使用线程池并行准备 characteristics 和 measurements 的数据
            with ThreadPoolExecutor(max_workers=8) as executor:
                char_future = executor.submit(
                    self._prepare_characteristics_data_with_existing,
                    parsed_data,
                    compu_method_map,
                    existing_char_dict,
                )
                
                meas_future = executor.submit(
                    self._prepare_measurements_data_with_existing,
                    parsed_data,
                    compu_method_map,
                    module,
                    existing_meas_dict,
                )
                
                # 等待并行准备完成
                char_to_create, char_to_update = char_future.result()
                meas_to_create, meas_to_update = meas_future.result()
            
            # 串行执行批量插入和更新（保证事务一致性）
            actual_char_created = 0
            if char_to_create:
                Characteristic.objects.bulk_create(char_to_create, batch_size=500)
                actual_char_created = len(char_to_create)
                stats["characteristics"] += actual_char_created
            if char_to_update:
                Characteristic.objects.bulk_update(
                    char_to_update,
                    [
                        "long_identifier",
                        "characteristic_type",
                        "ecu_address",
                        "record_layout",
                        "conversion_method",
                        "max_diff",
                        "lower_limit",
                        "upper_limit",
                        "number",
                        "module",
                        "work_package",
                        "updater",
                    ],
                    batch_size=500,
                )
            logging.info(
                "Characteristic 入库完成: 新增 %d 条, 更新 %d 条 (A2L文件ID: %s)",
                actual_char_created,
                len(char_to_update),
                self.a2l_file.id,
            )
            
            actual_meas_created = 0
            if meas_to_create:
                Measurement.objects.bulk_create(meas_to_create, batch_size=500)
                actual_meas_created = len(meas_to_create)
                stats["measurements"] += actual_meas_created
            if meas_to_update:
                Measurement.objects.bulk_update(
                    meas_to_update,
                    [
                        "long_identifier",
                        "datatype",
                        "conversion_method",
                        "resolution",
                        "accuracy",
                        "lower_limit",
                        "upper_limit",
                        "ecu_address",
                        "module",
                        "work_package",
                    ],
                    batch_size=500,
                )
            logging.info(
                "Measurement 入库完成: 新增 %d 条, 更新 %d 条 (A2L文件ID: %s)",
                actual_meas_created,
                len(meas_to_update),
                self.a2l_file.id,
            )
            
            # 其他相对独立的小数据量操作
            self._sync_axis_pts(parsed_data, stats)
            self._sync_axis_descrs(parsed_data, stats)
            self._sync_axis_pts_refs(parsed_data, stats)
            self._sync_record_layouts(parsed_data, module, stats)
            self._sync_axis_pts_x(parsed_data, module, stats)

        logging.info(
            "Axis 数据入库完成: axis_pts=%d, axis_descrs=%d, axis_pts_refs=%d, axis_pts_x=%d",
            stats["axis_pts"],
            stats["axis_descrs"],
            stats["axis_pts_refs"],
            stats["axis_pts_x"],
        )
        logging.info("成功入库 A2L 数据到文件 %s: %s", self.a2l_file.id, stats)
        return stats

    # ------------------------------------------------------------------
    # Individual sync helpers
    # ------------------------------------------------------------------
    def _sync_project(self, parsed_data: Dict, stats: Dict[str, int]) -> Optional[A2LProject]:
        proj_data = parsed_data.get("project")
        if not proj_data:
            return None

        project, created = A2LProject.objects.update_or_create(
            a2l_file=self.a2l_file,
            name=proj_data["name"],
            defaults={"long_identifier": proj_data.get("long_identifier", "")},
        )
        if created:
            stats["projects"] += 1
            logging.info("新增 A2LProject: %s (A2L文件ID: %s)", proj_data["name"], self.a2l_file.id)
        else:
            logging.info(
                "跳过已存在的 A2LProject: %s (A2L文件ID: %s)",
                proj_data["name"],
                self.a2l_file.id,
            )
        return project

    def _sync_module(
        self,
        parsed_data: Dict,
        project: Optional[A2LProject],
        stats: Dict[str, int],
    ) -> Optional[A2LModule]:
        mod_data = parsed_data.get("module")
        if not mod_data or not project:
            return None

        module, created = A2LModule.objects.update_or_create(
            project=project,
            name=mod_data["name"],
            defaults={"long_identifier": mod_data.get("long_identifier", "")},
        )
        if created:
            stats["modules"] += 1
            logging.info("新增 A2LModule: %s (Project: %s)", mod_data["name"], project.name)
        else:
            logging.info(
                "跳过已存在的 A2LModule: %s (Project: %s)",
                mod_data["name"],
                project.name,
            )
        return module

    # def _sync_module_parameters(
    #     self,
    #     parsed_data: Dict,
    #     module: Optional[A2LModule],
    #     stats: Dict[str, int],
    # ) -> None:
    #     mod_par_data = parsed_data.get("module_parameters")
    #     if not mod_par_data:
    #         return

    #     target_module = module or self.default_module
    #     if not target_module and mod_par_data.get("module_name"):
    #         target_module = A2LModule.objects.filter(
    #             project__a2l_file=self.a2l_file,
    #             name=mod_par_data.get("module_name"),
    #         ).first()
    #     if not target_module:
    #         logging.warning("未找到对应的 A2LModule，MODULE_PARAMETER 入库被跳过")
    #         return

    #     try:
    #         module_parameter, created = A2LModuleParameter.objects.update_or_create(
    #             module=target_module,
    #             defaults={
    #                 "version_identifier": mod_par_data.get("version_identifier", ""),
    #                 "supplier": mod_par_data.get("supplier", ""),
    #                 "customer": mod_par_data.get("customer", ""),
    #                 "customer_no": mod_par_data.get("customer_no", ""),
    #                 "user": mod_par_data.get("user", ""),
    #                 "phone_no": mod_par_data.get("phone_no", ""),
    #                 "ecu": mod_par_data.get("ecu", ""),
    #                 "cpu_type": mod_par_data.get("cpu_type", ""),
    #                 "no_of_interfaces": mod_par_data.get("no_of_interfaces", 1),
    #             },
    #         )
    #         if created:
    #             stats["module_parameters"] += 1
    #         else:
    #             logging.info("跳过已存在的 A2LModuleParameter (Module: %s)", target_module.name)
    #     except Exception as exc:
    #         logging.warning("入库 MODULE_PARAMETER 失败: %s", exc)

    def _sync_asap_version(self, parsed_data: Dict, stats: Dict[str, int]) -> None:
        asap2_data = parsed_data.get("asap2_version")
        if not asap2_data:
            return

        try:
            asap2_version, created = Asap2Version.objects.update_or_create(
                a2l_file=self.a2l_file,
                version_no=asap2_data.get("version_no", 1),
                upgrade_no=asap2_data.get("upgrade_no", 0),
            )
            if created:
                stats["asap2_version"] = 1
                logging.info("新增 Asap2Version: v%s.%s (A2L文件ID: %s)", asap2_data.get("version_no", 1), asap2_data.get("upgrade_no", 0), self.a2l_file.id)
            else:
                logging.info(
                    "跳过已存在的 Asap2Version: v%s.%s (A2L文件ID: %s)",
                    asap2_data.get("version_no", 1),
                    asap2_data.get("upgrade_no", 0),
                    self.a2l_file.id,
                )
        except Exception as exc:
            logging.warning("入库 ASAP2_VERSION 失败: %s", exc)

    def _sync_compu_methods(self, parsed_data: Dict, stats: Dict[str, int]) -> Tuple[Dict[str, CompuMethod], Dict[int, str]]:
        # 先保存旧的 CompuMethod ID 到名称的映射关系，用于后续查询现有 Characteristic 和 Measurement 时匹配
        old_compu_method_id_to_name: Dict[int, str] = {}
        for old_cm in CompuMethod.objects.filter(a2l_file=self.a2l_file):
            old_compu_method_id_to_name[old_cm.id] = old_cm.name
        
        # 先删除该 A2L 文件相关的所有 CompuMethod 记录，然后直接写入全部解析的数据
        # 注意：由于执行顺序是先创建 CompuMethod，再创建 Characteristic 和 Measurement，
        # 所以删除 CompuMethod 不会影响后续的创建（它们会引用新创建的 CompuMethod）
        deleted_count = CompuMethod.objects.filter(
            a2l_file=self.a2l_file
        ).delete()[0]
        if deleted_count > 0:
            logging.info("删除该 A2L 文件相关的旧 CompuMethod 记录: %d 条 (A2L文件ID: %s)", deleted_count, self.a2l_file.id)
        
        total = len(parsed_data.get("compu_methods", []))
        coeffs_cache: Dict[tuple, Coeffs] = {}
        compu_methods: Dict[str, CompuMethod] = {}
        created_count = 0
        skipped_errors = 0

        for cm_data in parsed_data.get("compu_methods", []):
            coeffs = None
            if cm_data.get("coeffs"):
                coeffs_key = self._build_coeffs_key(cm_data["coeffs"])
                coeffs = coeffs_cache.get(coeffs_key)
                if coeffs is None:
                    coeffs, created = Coeffs.objects.get_or_create(
                        a=coeffs_key[0],
                        b=coeffs_key[1],
                        c=coeffs_key[2],
                        d=coeffs_key[3],
                        e=coeffs_key[4],
                        f=coeffs_key[5],
                    )
                    coeffs_cache[coeffs_key] = coeffs
                    if created:
                        stats["coeffs"] += 1
            if coeffs is None:
                coeffs, _ = Coeffs.objects.get_or_create(a=0, b=1, c=0, d=0, e=0, f=1)

            # 由于已经删除了该 A2L 文件相关的所有旧记录，这里直接创建新记录
            # 不做任何去重处理，写入所有解析到的 CompuMethod
            # 注意：如果发生唯一约束冲突，整个事务会失败并回滚（要么全部成功，要么全部失败）
            parsed_name = cm_data["name"]
            compu_method = CompuMethod.objects.create(
                a2l_file=self.a2l_file,
                name=parsed_name,
                long_identifier=cm_data.get("long_identifier", ""),
                conversion_type=cm_data.get("conversion_type", "RAT_FUNC"),
                format_str=cm_data.get("format_str", ""),
                units=cm_data.get("units", ""),
                coefficient=coeffs,
            )
            compu_methods[compu_method.name] = compu_method
            stats["compu_methods"] += 1
            created_count += 1
            # logging.info("新增 CompuMethod: %s (A2L文件ID: %s)", parsed_name, self.a2l_file.id)
        
        logging.info(
            "CompuMethod 入库完成: 总数=%d, 新增=%d, 错误=%d (A2L文件ID: %s)",
            total, created_count, skipped_errors, self.a2l_file.id
        )

        return compu_methods, old_compu_method_id_to_name

    def _sync_measurements(
        self,
        parsed_data: Dict,
        compu_method_map: Dict[str, CompuMethod],
        module: Optional[A2LModule],
        stats: Dict[str, int],
    ) -> None:
        module_fallback = self.default_module or module
        if module_fallback is None:
            module_fallback = self._find_existing_module()
        if module_fallback is None:
            logging.warning("未能解析到默认的 A2LModule，测量量入库被跳过")
            return

        # 使用 (name, ecu_address, conversion_method_id) 作为唯一键
        existing_measurements = {}
        for item in Measurement.objects.filter(
            a2l_file=self.a2l_file
        ).select_related("conversion_method"):
            key = (item.name, item.ecu_address, item.conversion_method_id)
            existing_measurements[key] = item

        to_create: List[Measurement] = []
        to_update: List[Measurement] = []
        processed_keys: set[tuple] = set()

        for meas_data in parsed_data.get("measurements", []):
            name = meas_data["name"]
            conversion_method = compu_method_map.get(meas_data.get("conversion_method"))
            ecu_address = self._normalize_address(meas_data.get("ecu_address"))
            if conversion_method is None:
                logging.error(
                    "Measurement 缺少转换方法: name=%s conversion_key=%s record=%s (A2L文件ID: %s)",
                    name,
                    meas_data.get("conversion_method"),
                    meas_data,
                    self.a2l_file.id,
                )
                continue
            
            # 使用组合键进行判重
            key = (name, ecu_address, conversion_method.id)
            if key in processed_keys:
                logging.warning(
                    "检测到重复的 Measurement: name=%s, ecu_address=%s, conversion_method_id=%s (A2L文件ID: %s)，仅保留首次解析的记录",
                    name,
                    ecu_address,
                    conversion_method.id,
                    self.a2l_file.id,
                )
                continue
            
            defaults = {
                "long_identifier": meas_data.get("long_identifier", ""),
                "datatype": meas_data.get("datatype", "UBYTE"),
                "conversion_method": conversion_method,
                "resolution": meas_data.get("resolution", 0),
                "accuracy": meas_data.get("accuracy", 0.0),
                "lower_limit": meas_data.get("lower_limit") or 0.0,
                "upper_limit": meas_data.get("upper_limit") or 0.0,
                "ecu_address": ecu_address,
                "module": module_fallback,
                "work_package": self.default_work_package,  # 默认使用"未绑定"工作包，后续可手工绑定
            }

            existing = existing_measurements.get(key)
            if existing:
                changed = False
                for field, value in defaults.items():
                    # 对于 ForeignKey 字段，比较时使用 field_id
                    existing_value = self._safe_get_field_value(existing, field)
                    # 如果 value 是对象，获取其 ID 用于比较
                    value_for_compare = value.id if hasattr(value, 'id') else value
                    if existing_value != value_for_compare:
                        # 使用安全设置方法，对于 ForeignKey 字段会自动使用 field_id
                        self._safe_set_field_value(existing, field, value)
                        changed = True
                if changed:
                    to_update.append(existing)
                processed_keys.add(key)
            else:
                to_create.append(Measurement(a2l_file=self.a2l_file, name=name, **defaults))
                processed_keys.add(key)

        if to_create:
            Measurement.objects.bulk_create(to_create, batch_size=500)
            stats["measurements"] += len(to_create)
        if to_update:
            Measurement.objects.bulk_update(
                to_update,
                [
                    "long_identifier",
                    "datatype",
                    "conversion_method",
                    "resolution",
                    "accuracy",
                    "lower_limit",
                    "upper_limit",
                    "ecu_address",
                    "module",
                    "work_package",
                ],
                batch_size=500,
            )

        logging.info(
            "Measurement 入库完成: 新增 %d 条, 更新 %d 条 (A2L文件ID: %s)",
            len(to_create),
            len(to_update),
            self.a2l_file.id,
        )

    def _sync_characteristics(
        self,
        parsed_data: Dict,
        compu_method_map: Dict[str, CompuMethod],
        stats: Dict[str, int],
    ) -> None:
        module_fallback = self.default_module or self._find_existing_module()
        if module_fallback is None:
            logging.warning("未能解析到默认的 A2LModule，标定量入库被跳过")
            return

        # 使用 (name, ecu_address, conversion_method_id) 作为唯一键
        existing_characteristics = {}
        for item in Characteristic.objects.filter(
            a2l_file=self.a2l_file
        ).select_related("conversion_method"):
            key = (item.name, item.ecu_address, item.conversion_method_id)
            existing_characteristics[key] = item

        to_create: List[Characteristic] = []
        to_update: List[Characteristic] = []
        processed_keys: set[tuple] = set()

        for char_data in parsed_data.get("characteristics", []):
            name = char_data["name"]
            conversion_method = compu_method_map.get(char_data.get("conversion_method"))
            ecu_address = self._normalize_address(char_data.get("ecu_address"))
            if conversion_method is None:
                logging.error(
                    "Characteristic 缺少转换方法: name=%s conversion_key=%s record=%s (A2L文件ID: %s)",
                    name,
                    char_data.get("conversion_method"),
                    char_data,
                    self.a2l_file.id,
                )
                continue
            
            # 使用组合键进行判重
            key = (name, ecu_address, conversion_method.id)
            if key in processed_keys:
                logging.warning(
                    "检测到重复的 Characteristic: name=%s, ecu_address=%s, conversion_method_id=%s (A2L文件ID: %s)，仅保留首次解析的记录",
                    name,
                    ecu_address,
                    conversion_method.id,
                    self.a2l_file.id,
                )
                continue
            
            number_value = self._normalize_characteristic_number(
                char_data.get("number", 0),
                name,
                char_data,
            )
            defaults = {
                "long_identifier": char_data.get("long_identifier", ""),
                "characteristic_type": char_data.get("characteristic_type", "VALUE"),
                "ecu_address": ecu_address,
                "record_layout": char_data.get("record_layout", ""),
                "conversion_method": conversion_method,
                "max_diff": char_data.get("max_diff", 0.0),
                "lower_limit": char_data.get("lower_limit") or 0.0,
                "upper_limit": char_data.get("upper_limit") or 0.0,
                "number": number_value,
                "module": module_fallback,
                "work_package": self.default_work_package,  # 默认使用"未绑定"工作包，后续可手工绑定
                "updater": self.updater,
            }

            existing = existing_characteristics.get(key)
            if existing:
                changed = False
                for field, value in defaults.items():
                    # 对于 ForeignKey 字段，比较时使用 field_id
                    existing_value = self._safe_get_field_value(existing, field)
                    # 如果 value 是对象，获取其 ID 用于比较
                    value_for_compare = value.id if hasattr(value, 'id') else value
                    if existing_value != value_for_compare:
                        # 使用安全设置方法，对于 ForeignKey 字段会自动使用 field_id
                        self._safe_set_field_value(existing, field, value)
                        changed = True
                if changed:
                    to_update.append(existing)
                processed_keys.add(key)
            else:
                to_create.append(Characteristic(a2l_file=self.a2l_file, name=name, **defaults))
                processed_keys.add(key)

        if to_create:
            Characteristic.objects.bulk_create(to_create, batch_size=500)
            stats["characteristics"] += len(to_create)
        if to_update:
            Characteristic.objects.bulk_update(
                to_update,
                [
                    "long_identifier",
                    "characteristic_type",
                    "ecu_address",
                    "record_layout",
                    "conversion_method",
                    "max_diff",
                    "lower_limit",
                    "upper_limit",
                    "number",
                    "module",
                    "work_package",
                    "updater",
                ],
                batch_size=500,
            )

        logging.info(
            "Characteristic 入库完成: 新增 %d 条, 更新 %d 条, 跳过 %d 条",
            len(to_create),
            len(to_update),
            max(0, len(parsed_data.get("characteristics", [])) - len(to_create) - len(to_update)),
        )

    def _prepare_characteristics_data_with_existing(
        self,
        parsed_data: Dict,
        compu_method_map: Dict[str, CompuMethod],
        existing_characteristics: Dict,
    ) -> tuple[List[Characteristic], List[Characteristic]]:
        """准备标定量数据（不执行数据库操作，可并行调用）
        
        基于 unique_together (a2l_file, name, ecu_address, conversion_method) 进行判重
        对于已存在的记录进行更新，对于新记录进行创建
        
        Args:
            existing_characteristics: 已存在的记录字典，key 为 (name, ecu_address, conversion_method_name)
        """
        module_fallback = self.default_module or self._find_existing_module()
        if module_fallback is None:
            return [], []

        to_create: List[Characteristic] = []
        to_update: List[Characteristic] = []
        processed_keys: set[tuple] = set()

        for char_data in parsed_data.get("characteristics", []):
            name = char_data["name"]
            conversion_method = compu_method_map.get(char_data.get("conversion_method"))
            ecu_address = self._normalize_address(char_data.get("ecu_address"))
            if conversion_method is None:
                logging.error(
                    "Characteristic 缺少转换方法: name=%s conversion_key=%s record=%s (A2L文件ID: %s)",
                    name,
                    char_data.get("conversion_method"),
                    char_data,
                    self.a2l_file.id,
                )
                continue
            
            # 使用组合键进行判重（基于 unique_together）
            # 注意：使用 conversion_method.name 而不是 conversion_method.id，因为 CompuMethod 采用"先删除后创建"策略，ID 会变化
            conversion_method_name = conversion_method.name if conversion_method else ""
            key = (name, ecu_address, conversion_method_name)
            if key in processed_keys:
                logging.warning(
                    "检测到重复的 Characteristic: name=%s, ecu_address=%s, conversion_method=%s (A2L文件ID: %s)，仅保留首次解析的记录",
                    name,
                    ecu_address,
                    conversion_method_name,
                    self.a2l_file.id,
                )
                continue
            
            number_value = self._normalize_characteristic_number(
                char_data.get("number", 0),
                name,
                char_data,
            )
            defaults = {
                "long_identifier": char_data.get("long_identifier", ""),
                "characteristic_type": char_data.get("characteristic_type", "VALUE"),
                "ecu_address": ecu_address,
                "record_layout": char_data.get("record_layout", ""),
                "conversion_method": conversion_method,
                "max_diff": char_data.get("max_diff", 0.0),
                "lower_limit": char_data.get("lower_limit") or 0.0,
                "upper_limit": char_data.get("upper_limit") or 0.0,
                "number": number_value,
                "module": module_fallback,
                "work_package": self.default_work_package,  # 默认使用"未绑定"工作包，后续可手工绑定
                "updater": self.updater,
            }

            existing = existing_characteristics.get(key)
            if existing:
                # 更新现有记录
                changed = False
                for field, value in defaults.items():
                    # 对于 ForeignKey 字段，比较时使用 field_id
                    existing_value = self._safe_get_field_value(existing, field)
                    # 如果 value 是对象，获取其 ID 用于比较
                    value_for_compare = value.id if hasattr(value, 'id') else value
                    if existing_value != value_for_compare:
                        # 使用安全设置方法，对于 ForeignKey 字段会自动使用 field_id
                        self._safe_set_field_value(existing, field, value)
                        changed = True
                if changed:
                    to_update.append(existing)
                processed_keys.add(key)
            else:
                # 创建新记录
                to_create.append(Characteristic(a2l_file=self.a2l_file, name=name, **defaults))
                processed_keys.add(key)

        return to_create, to_update

    def _prepare_measurements_data_with_existing(
        self,
        parsed_data: Dict,
        compu_method_map: Dict[str, CompuMethod],
        module: Optional[A2LModule],
        existing_measurements: Dict,
    ) -> tuple[List[Measurement], List[Measurement]]:
        """准备测量量数据（不执行数据库操作，可并行调用）
        
        基于 unique_together (a2l_file, name, ecu_address, conversion_method) 进行判重
        对于已存在的记录进行更新，对于新记录进行创建
        
        Args:
            existing_measurements: 已存在的记录字典，key 为 (name, ecu_address, conversion_method_name)
        """
        module_fallback = self.default_module or module
        if module_fallback is None:
            module_fallback = self._find_existing_module()
        if module_fallback is None:
            return [], []

        to_create: List[Measurement] = []
        to_update: List[Measurement] = []
        processed_keys: set[tuple] = set()

        for meas_data in parsed_data.get("measurements", []):
            name = meas_data["name"]
            conversion_method = compu_method_map.get(meas_data.get("conversion_method"))
            ecu_address = self._normalize_address(meas_data.get("ecu_address"))
            if conversion_method is None:
                logging.error(
                    "Measurement 缺少转换方法: name=%s conversion_key=%s record=%s (A2L文件ID: %s)",
                    name,
                    meas_data.get("conversion_method"),
                    meas_data,
                    self.a2l_file.id,
                )
                continue
            
            # 使用组合键进行判重（基于 unique_together）
            # 注意：使用 conversion_method.name 而不是 conversion_method.id，因为 CompuMethod 采用"先删除后创建"策略，ID 会变化
            conversion_method_name = conversion_method.name if conversion_method else ""
            key = (name, ecu_address, conversion_method_name)
            if key in processed_keys:
                logging.warning(
                    "检测到重复的 Measurement: name=%s, ecu_address=%s, conversion_method=%s (A2L文件ID: %s)，仅保留首次解析的记录",
                    name,
                    ecu_address,
                    conversion_method_name,
                    self.a2l_file.id,
                )
                continue
            
            defaults = {
                "long_identifier": meas_data.get("long_identifier", ""),
                "datatype": meas_data.get("datatype", "UBYTE"),
                "conversion_method": conversion_method,
                "resolution": meas_data.get("resolution", 0),
                "accuracy": meas_data.get("accuracy", 0.0),
                "lower_limit": meas_data.get("lower_limit") or 0.0,
                "upper_limit": meas_data.get("upper_limit") or 0.0,
                "ecu_address": ecu_address,
                "module": module_fallback,
                "work_package": self.default_work_package,  # 默认使用"未绑定"工作包，后续可手工绑定
            }

            existing = existing_measurements.get(key)
            if existing:
                # 更新现有记录
                changed = False
                for field, value in defaults.items():
                    # 对于 ForeignKey 字段，比较时使用 field_id
                    existing_value = self._safe_get_field_value(existing, field)
                    # 如果 value 是对象，获取其 ID 用于比较
                    value_for_compare = value.id if hasattr(value, 'id') else value
                    if existing_value != value_for_compare:
                        # 使用安全设置方法，对于 ForeignKey 字段会自动使用 field_id
                        self._safe_set_field_value(existing, field, value)
                        changed = True
                if changed:
                    to_update.append(existing)
                processed_keys.add(key)
            else:
                # 创建新记录
                to_create.append(Measurement(a2l_file=self.a2l_file, name=name, **defaults))
                processed_keys.add(key)

        return to_create, to_update

    def _sync_axis_pts(self, parsed_data: Dict, stats: Dict[str, int]) -> None:
        """同步轴点定义（AXIS_PTS）
        
        基于 unique_together (a2l_file, name, address, conversion_method) 进行判重
        """
        created_count = 0
        updated_count = 0
        skipped_errors = 0
        total = len(parsed_data.get("axis_pts", []))
        
        for axis_pts_data in parsed_data.get("axis_pts", []):
            name = axis_pts_data.get("name")
            if not name:
                skipped_errors += 1
                continue
                
            address = self._normalize_address(axis_pts_data.get("ecu_address", 0))
            conversion_method = axis_pts_data.get("conversion_method", "")

            try:
                axis_pts, created = AxisPts.objects.update_or_create(
                    a2l_file=self.a2l_file,
                    name=name,
                    address=address,
                    conversion_method=conversion_method,
                    defaults={
                        "long_identifier": axis_pts_data.get("long_identifier", ""),
                        "input_quantity": axis_pts_data.get("input_quantity", ""),
                        "record_layout": axis_pts_data.get("record_layout", ""),
                        "max_diff": axis_pts_data.get("max_diff", 0.0),
                        "max_axis_points": axis_pts_data.get("max_axis_points", 0),
                        "lower_limit": axis_pts_data.get("lower_limit") or 0.0,
                        "upper_limit": axis_pts_data.get("upper_limit") or 0.0,
                        "module": self.default_module or self._find_existing_module(),
                    },
                )
                if created:
                    stats["axis_pts"] += 1
                    created_count += 1
                else:
                    updated_count += 1
                    logging.debug(
                        "更新已存在的 AxisPts: name=%s, address=%s, conversion_method=%s (A2L文件ID: %s)",
                        name,
                        address,
                        conversion_method,
                        self.a2l_file.id,
                    )
            except Exception as exc:
                logging.warning(
                    "入库 AXIS_PTS (name=%s, address=%s, conversion_method=%s) 失败: %s",
                    name,
                    address,
                    conversion_method,
                    exc,
                )
                skipped_errors += 1
        
        logging.info(
            "AxisPts 入库完成: 总数=%d, 新增=%d, 更新=%d, 错误=%d (A2L文件ID: %s)",
            total,
            created_count,
            updated_count,
            skipped_errors,
            self.a2l_file.id,
        )

    def _sync_axis_descrs(self, parsed_data: Dict, stats: Dict[str, int]) -> None:
        # 先删除该 A2L 文件相关的所有 AxisDescr 记录，确保数据一致性
        # 这样可以避免重复导入时出现数据不一致的问题
        characteristics_ids = Characteristic.objects.filter(
            a2l_file=self.a2l_file
        ).values_list('id', flat=True)
        deleted_count = AxisDescr.objects.filter(
            characteristic_id__in=characteristics_ids
        ).delete()[0]
        if deleted_count > 0:
            logging.info("删除该 A2L 文件相关的旧 AxisDescr 记录: %d 条 (A2L文件ID: %s)", deleted_count, self.a2l_file.id)
        
        created_count = 0
        updated_count = 0
        skipped_no_char_name = 0
        skipped_no_characteristic = 0
        skipped_errors = 0
        total = len(parsed_data.get("axis_descrs", []))
        
        for axis_descr in parsed_data.get("axis_descrs", []):
            char_name = axis_descr.get("characteristic_name")
            if not char_name:
                skipped_no_char_name += 1
                continue
            # 注意：由于 unique_together 包含 (a2l_file, name, ecu_address, conversion_method)，
            # 同一个 A2L 文件中可能有多个同名的 Characteristic，这里使用 .first() 获取第一个匹配的记录
            characteristics = Characteristic.objects.filter(
                a2l_file=self.a2l_file, name=char_name
            )
            if not characteristics.exists():
                logging.warning("AxisDescr 找不到对应的 Characteristic: %s (A2L文件ID: %s)", char_name, self.a2l_file.id)
                skipped_no_characteristic += 1
                continue
            if characteristics.count() > 1:
                logging.warning(
                    "AxisDescr 找到多个同名的 Characteristic: %s (A2L文件ID: %s, 共 %d 个)，使用第一个匹配的记录",
                    char_name,
                    self.a2l_file.id,
                    characteristics.count(),
                )
            characteristic = characteristics.first()
            # 注意：axis_pts_ref_name 只是用于记录第一个引用的名称，不应该作为创建 AxisDescr 的必要条件
            # 即使没有 axis_pts_ref_name，也应该创建 AxisDescr，因为对应的 axis_pts_refs 可能仍然存在
            defaults = {
                "input_quantity": axis_descr.get("input_quantity", ""),
                "conversion_method": axis_descr.get("conversion_method", ""),
                "max_axis_points": axis_descr.get("max_axis_points", 0),
                "lower_limit": axis_descr.get("lower_limit") or 0.0,
                "upper_limit": axis_descr.get("upper_limit") or 0.0,
                "attribute": axis_descr.get("attribute", ""),
            }
            try:
                # 由于已经删除了该 A2L 文件相关的所有旧记录，这里直接创建新记录
                AxisDescr.objects.create(
                    characteristic=characteristic,
                    **defaults
                )
                stats["axis_descrs"] += 1
                created_count += 1
            except Exception as exc:
                logging.warning("入库 AXIS_DESCR (char=%s) 失败: %s", char_name, exc)
                skipped_errors += 1
        processed_count = created_count + updated_count
        logging.info(
            "AxisDescr 入库完成: 总数=%d, 处理=%d(新增=%d, 更新=%d), 跳过(无char_name)=%d, 跳过(无Characteristic)=%d, 错误=%d",
            total, processed_count, created_count, updated_count, skipped_no_char_name, skipped_no_characteristic, skipped_errors
        )

    def _sync_axis_pts_refs(self, parsed_data: Dict, stats: Dict[str, int]) -> None:
        # 先删除该 A2L 文件相关的所有 AxisPtsRef 记录，确保数据一致性
        # 这样可以避免重复导入时出现数据不一致的问题
        characteristics_ids = Characteristic.objects.filter(
            a2l_file=self.a2l_file
        ).values_list('id', flat=True)
        axis_descrs_ids = AxisDescr.objects.filter(
            characteristic_id__in=characteristics_ids
        ).values_list('id', flat=True)
        deleted_count = AxisPtsRef.objects.filter(
            axis_descr_id__in=axis_descrs_ids
        ).delete()[0]
        if deleted_count > 0:
            logging.info("删除该 A2L 文件相关的旧 AxisPtsRef 记录: %d 条 (A2L文件ID: %s)", deleted_count, self.a2l_file.id)
        
        created_count = 0
        skipped_no_data = 0
        skipped_no_characteristic = 0
        skipped_no_axis_descr = 0
        skipped_errors = 0
        total = len(parsed_data.get("axis_pts_refs", []))
        
        for axis_ref in parsed_data.get("axis_pts_refs", []):
            char_name = axis_ref.get("characteristic_name")
            axis_points = axis_ref.get("axis_points")
            if not char_name or not axis_points:
                skipped_no_data += 1
                continue
            # 注意：由于 unique_together 包含 (a2l_file, name, ecu_address, conversion_method)，
            # 同一个 A2L 文件中可能有多个同名的 Characteristic，这里使用 .first() 获取第一个匹配的记录
            characteristics = Characteristic.objects.filter(
                a2l_file=self.a2l_file, name=char_name
            )
            if not characteristics.exists():
                logging.warning("AxisPtsRef 找不到对应的 Characteristic: %s (A2L文件ID: %s)", char_name, self.a2l_file.id)
                skipped_no_characteristic += 1
                continue
            if characteristics.count() > 1:
                logging.warning(
                    "AxisPtsRef 找到多个同名的 Characteristic: %s (A2L文件ID: %s, 共 %d 个)，使用第一个匹配的记录",
                    char_name,
                    self.a2l_file.id,
                    characteristics.count(),
                )
            characteristic = characteristics.first()
            # 通过 characteristic 和 attribute 查找对应的 AxisDescr
            # 如果 axis_ref 中有 attribute 信息，使用它来精确匹配
            attribute = axis_ref.get("attribute", "")
            axis_descr = None
            if attribute:
                axis_descr = AxisDescr.objects.filter(
                    characteristic=characteristic,
                    attribute=attribute,
                ).first()
            if not axis_descr:
                # 如果没有 attribute 或通过 attribute 找不到，尝试通过已存在的 AxisPtsRef 关系查询
                axis_descr = AxisDescr.objects.filter(
                    characteristic=characteristic,
                    axis_pts_ref__axis_points=axis_points,
                ).first()
            if not axis_descr:
                # 如果还是找不到，取第一个匹配的 AxisDescr（作为后备方案）
                axis_descr = AxisDescr.objects.filter(
                    characteristic=characteristic,
                ).first()
            if not axis_descr:
                logging.warning(
                    "AxisPtsRef 找不到对应的 AxisDescr: char=%s, axis_points=%s, attribute=%s (A2L文件ID: %s)",
                    char_name,
                    axis_points,
                    attribute,
                    self.a2l_file.id,
                )
                skipped_no_axis_descr += 1
                continue
            try:
                # 由于已经删除了该 A2L 文件相关的所有旧记录，这里直接创建新记录
                AxisPtsRef.objects.create(
                    axis_descr=axis_descr,
                    axis_points=axis_points,
                )
                stats["axis_pts_refs"] += 1
                created_count += 1
            except Exception as exc:
                logging.warning(
                    "入库 AXIS_PTS_REF (char=%s, axis=%s) 失败: %s",
                    char_name,
                    axis_points,
                    exc,
                )
                skipped_errors += 1
        logging.info(
            "AxisPtsRef 入库完成: 总数=%d, 新增=%d, 跳过(无数据)=%d, 跳过(无Characteristic)=%d, 跳过(无AxisDescr)=%d, 错误=%d (A2L文件ID: %s)",
            total, created_count, skipped_no_data, skipped_no_characteristic, skipped_no_axis_descr, skipped_errors, self.a2l_file.id
        )

    def _sync_axis_pts_x(
        self,
        parsed_data: Dict,
        module: Optional[A2LModule],
        stats: Dict[str, int],
    ) -> None:
        # 先删除该 A2L 文件相关的所有 AxisPtsX 记录，然后直接写入全部解析的数据
        # 通过 RecordLayout.module.project.a2l_file 关联查找并删除
        axis_pts_x_list = parsed_data.get("axis_pts_x", [])
        if not axis_pts_x_list:
            return
        
        target_module = module or self.default_module or self._find_existing_module()
        if not target_module:
            logging.warning("未能解析到默认的 A2LModule，AxisPtsX 入库被跳过")
            return
        
        # 删除该 A2L 文件相关的所有 RecordLayout 关联的所有 AxisPtsX 记录
        # 通过 RecordLayout.module.project.a2l_file 来关联查找
        deleted_count = AxisPtsX.objects.filter(
            record_layout__module__project__a2l_file=self.a2l_file
        ).delete()[0]
        if deleted_count > 0:
            logging.info("删除该 A2L 文件相关的旧 AxisPtsX 记录: %d 条 (A2L文件ID: %s)", 
                       deleted_count, self.a2l_file.id)
        
        # 重新创建所有 AxisPtsX
        # 由于 AxisPtsX 的 record_layout 已改为 ForeignKey，一个 RecordLayout 可以关联多个 AxisPtsX
        # 采用"先删除后创建"策略，不需要唯一约束，允许重复的 (record_layout, position, datatype) 组合
        created_count = 0
        skipped_count = 0
        for axis_pts_x in axis_pts_x_list:
            record_layout_name = axis_pts_x.get("record_layout_name")
            if not record_layout_name:
                skipped_count += 1
                continue
            
            record_layout = RecordLayout.objects.filter(
                module=target_module,
                name=record_layout_name,
            ).first()
            if not record_layout:
                skipped_count += 1
                continue
            
            try:
                AxisPtsX.objects.create(
                    record_layout=record_layout,
                    position=axis_pts_x.get("position", 0),
                    datatype=axis_pts_x.get("datatype", ""),
                    index_incr=axis_pts_x.get("index_incr", ""),
                    addressing=axis_pts_x.get("addressing", ""),
                )
                stats["axis_pts_x"] += 1
                created_count += 1
            except Exception as exc:
                logging.warning(
                    "入库 AXIS_PTS_X (record_layout=%s, position=%s, datatype=%s) 失败: %s",
                    record_layout_name,
                    axis_pts_x.get("position", 0),
                    axis_pts_x.get("datatype", ""),
                    exc,
                )
                skipped_count += 1
        
        logging.info(
            "AxisPtsX 入库完成: 新增 %d 条, 跳过 %d 条 (A2L文件ID: %s)",
            created_count, skipped_count, self.a2l_file.id
        )

    def _sync_record_layouts(
        self,
        parsed_data: Dict,
        module: Optional[A2LModule],
        stats: Dict[str, int],
    ) -> None:
        # 先删除该 A2L 文件相关的所有 RecordLayout 记录，然后直接写入全部解析的数据
        # 注意：由于执行顺序是先创建 RecordLayout，再创建 AxisPtsX（AxisPtsX 有 OneToOneField 关联到 RecordLayout），
        # 所以需要先删除 AxisPtsX，再删除 RecordLayout
        # 通过 module.project.a2l_file 关联查找并删除
        
        # 先收集所有需要处理的 module
        modules_to_process = set()
        target_module = module or self.default_module
        if target_module:
            modules_to_process.add(target_module)
        
        for rl_data in parsed_data.get("record_layouts", []):
            if rl_data.get("module_name"):
                module_by_name = A2LModule.objects.filter(
                    project__a2l_file=self.a2l_file,
                    name=rl_data.get("module_name"),
                ).first()
                if module_by_name:
                    modules_to_process.add(module_by_name)
        
        # 先删除所有相关 module 的 AxisPtsX（因为 AxisPtsX 有 OneToOneField 关联到 RecordLayout）
        for mod in modules_to_process:
            # 获取该 module 的所有 RecordLayout
            record_layouts = RecordLayout.objects.filter(module=mod)
            # 删除这些 RecordLayout 关联的 AxisPtsX
            axis_pts_x_deleted = AxisPtsX.objects.filter(record_layout__in=record_layouts).delete()[0]
            if axis_pts_x_deleted > 0:
                logging.info("删除该 Module 相关的旧 AxisPtsX 记录: %d 条 (Module: %s, A2L文件ID: %s)", 
                           axis_pts_x_deleted, mod.name, self.a2l_file.id)
        
        # 然后删除所有相关 module 的 RecordLayout
        total_deleted = 0
        for mod in modules_to_process:
            deleted_count = RecordLayout.objects.filter(module=mod).delete()[0]
            if deleted_count > 0:
                logging.info("删除该 Module 相关的旧 RecordLayout 记录: %d 条 (Module: %s, A2L文件ID: %s)", 
                           deleted_count, mod.name, self.a2l_file.id)
                total_deleted += deleted_count
        
        # 重新创建所有 RecordLayout
        created_count = 0
        skipped_count = 0
        for rl_data in parsed_data.get("record_layouts", []):
            target_module = module or self.default_module
            if not target_module and rl_data.get("module_name"):
                target_module = A2LModule.objects.filter(
                    project__a2l_file=self.a2l_file,
                    name=rl_data.get("module_name"),
                ).first()
            if not target_module:
                skipped_count += 1
                continue

            try:
                RecordLayout.objects.create(
                    module=target_module,
                    name=rl_data["name"],
                )
                stats["record_layouts"] += 1
                created_count += 1
                # logging.info("新增 RecordLayout: %s (Module: %s)", rl_data["name"], target_module.name)
            except Exception as exc:
                logging.warning("入库 RECORD_LAYOUT %s 失败: %s", rl_data.get("name"), exc)
                skipped_count += 1
        
        if skipped_count > 0:
            logging.info("RecordLayout 入库完成: 新增 %d 条, 跳过 %d 条 (A2L文件ID: %s)", 
                        created_count, skipped_count, self.a2l_file.id)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def _resolve_default_module(self) -> Optional[A2LModule]:
        if not self.module_id:
            return None
        try:
            return A2LModule.objects.get(id=self.module_id)
        except A2LModule.DoesNotExist:
            logging.warning("指定的 module_id=%s 不存在，将跳过 module 关联", self.module_id)
            return None

    def _resolve_default_unbound_work_package(self) -> WorkPackage:
        """获取或创建默认的"未绑定"工作包，用于暂时关联测量量/标定量，后续可手工绑定。"""
        work_package, created = WorkPackage.objects.get_or_create(
            name="未绑定",
            owner="system",
            deleted=False,  # 确保查找的是未删除的工作包
            defaults={
                "parent_id": 0,
                "remark": "系统默认工作包，用于暂时关联未绑定工作包的标定量和测量量，后续可手工绑定到具体工作包",
                "deleted": False,  # 创建时明确设置为未删除
            },
        )
        # 如果找到的工作包被标记为删除，恢复它
        if not created and work_package.deleted:
            work_package.deleted = False
            work_package.save(update_fields=['deleted'])
            logging.info("恢复已删除的默认工作包: 未绑定 (ID: %s)", work_package.id)
        return work_package

    def _find_existing_module(self) -> Optional[A2LModule]:
        """尝试从数据库中找到一个已存在的模块作为默认值。"""
        try:
            return (
                A2LModule.objects.filter(project__a2l_file=self.a2l_file)
                .order_by("id")
                .first()
            )
        except Exception:
            return None

    def _normalize_characteristic_number(self, value, name: str, record: Dict) -> int:
        """Safely normalize the CHARACTERISTIC 'number' field and log invalid cases."""
        if value is None:
            return 0

        if isinstance(value, (int, float)):
            return int(value)

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0
            try:
                return int(float(stripped))
            except ValueError:
                logging.warning(
                    "Characteristic number 字段解析失败: name=%s raw=%r record=%s (A2L文件ID: %s)",
                    name,
                    value,
                    record,
                    self.a2l_file.id,
                )
                return 0

        extracted = getattr(value, "number", None)
        if extracted is not None:
            try:
                return int(extracted)
            except (TypeError, ValueError):
                logging.warning(
                    "Characteristic number 字段提取失败: name=%s raw=%r record=%s (A2L文件ID: %s)",
                    name,
                    value,
                    record,
                    self.a2l_file.id,
                )
                return 0

        logging.warning(
            "Characteristic number 字段类型异常: name=%s raw=%r record=%s (A2L文件ID: %s)",
            name,
            value,
            record,
            self.a2l_file.id,
        )
        return 0

    @staticmethod
    def _build_coeffs_key(coeffs_data: Dict) -> tuple:
        return (
            coeffs_data.get("a", 0.0),
            coeffs_data.get("b", 1.0),
            coeffs_data.get("c", 0.0),
            coeffs_data.get("d", 0.0),
            coeffs_data.get("e", 0.0),
            coeffs_data.get("f", 1.0),
        )

    def _build_compu_method_map(self, existing_map: Dict[str, CompuMethod]) -> Dict[str, CompuMethod]:
        compu_method_map = dict(existing_map)
        for cm in CompuMethod.objects.filter(a2l_file=self.a2l_file):
            compu_method_map.setdefault(cm.name, cm)
        return compu_method_map

    @staticmethod
    def _normalize_address(value) -> int:
        if value is None:
            return 0
        if isinstance(value, str):
            value = value.strip()
            try:
                return int(value, 16) if value.lower().startswith("0x") or value.lower().startswith("-0x") else int(value)
            except ValueError:
                return 0
        if isinstance(value, (int, float)):
            return int(value)
        return 0

