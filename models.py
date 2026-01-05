class WorkPackage(models.Model):
    """标定工作包：归类用"""

    name = models.CharField('工作包名称', max_length=64)
    parent_id = models.IntegerField('父模块id', default=0)
    owner = models.CharField('拥有人', max_length=32)
    create_time = models.DateTimeField('创建时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)
    deleted = models.BooleanField('删除', default=False)
    remark = models.TextField('备注', blank=True, default='')


    class Meta:
        db_table = 'cal_work_package'
        verbose_name = '标定工作包'

    def __str__(self):
        return self.name


def get_default_unbound_work_package():
    """获取或创建默认的"未绑定"工作包，用于暂时关联未绑定工作包的标定量和测量量。
    
    注意：此函数确保"未绑定"工作包存在，如果不存在则创建。
    如果数据库为空，会自动创建"未绑定"工作包。
    如果确定"未绑定"工作包总是存在且ID为1，可以直接使用 default=1。
    """
    try:
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
        if created:
            logger.info("自动创建默认工作包: 未绑定 (ID: %s)", work_package.id)
        return work_package.id
    except Exception as e:
        logger.error("创建默认工作包失败: %s", e)
        # 如果创建失败，尝试查找第一个工作包（作为后备方案）
        try:
            first_wp = WorkPackage.objects.first()
            if first_wp:
                logger.warning("使用第一个工作包作为默认值: %s (ID: %s)", first_wp.name, first_wp.id)
                return first_wp.id
        except Exception:
            pass
        # 如果都失败了，抛出异常
        raise ValueError(f"无法获取或创建默认工作包: {e}")



class A2LProject(models.Model):
    """A2L 工程（PROJECT）"""

    a2l_file = models.OneToOneField(
        A2LFile,
        verbose_name='关联A2L文件',
        on_delete=models.DO_NOTHING,
        related_name='projects',
        db_constraint=False
    )

    name = models.CharField('名称', max_length=128)
    long_identifier = models.TextField('长标识符', blank=True, default='')
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cal_a2l_project'
        verbose_name = 'A2L工程'
        unique_together = ('a2l_file', 'name')

    def __str__(self):
        return self.name


class A2LModule(models.Model):
    """A2L 模块（MODULE）"""

    project = models.ForeignKey(
        A2LProject,
        verbose_name='所属工程',
        on_delete=models.DO_NOTHING,
        related_name='modules',
        db_constraint=False
    )

    name = models.CharField('名称', max_length=128)
    long_identifier = models.TextField('长标识符', blank=True, default='')
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cal_a2l_module'
        verbose_name = 'A2L模块'
        unique_together = ('project', 'name')

    def __str__(self):
        return self.name


class A2LModuleParameter(models.Model):
    """模块参数（MODULE_PARAMETER）
    /begin MOD_PAR
    ""
    VERSION          "Entry_XCP_1"
    SUPPLIER         "Xp Supply"
    CUSTOMER         "Customer"
    CUSTOMER_NO      "XPENG MOTORS"
    USER             ""
    PHONE_NO         ""
    ECU              "E38BMS"
    CPU_TYPE         "RH850U2A"
    NO_OF_INTERFACES 1
    ...
    """

    module = models.OneToOneField(
        A2LModule,
        verbose_name='所属模块',
        on_delete=models.DO_NOTHING,
        related_name='module_parameters',
        db_constraint=False
    )

    version_identifier = models.CharField('版本标识', max_length=128)

    # MOD_PAR 顶层字段
    supplier = models.CharField('供应商', max_length=128, blank=True, default='')
    customer = models.CharField('客户', max_length=128, blank=True, default='')
    customer_no = models.CharField('客户编号', max_length=128, blank=True, default='')
    user = models.CharField('用户', max_length=64, blank=True, default='')
    phone_no = models.CharField('联系电话', max_length=32, blank=True, default='')
    ecu = models.CharField('ECU名称', max_length=64, blank=True, default='')
    cpu_type = models.CharField('CPU类型', max_length=64, blank=True, default='')
    no_of_interfaces = models.PositiveIntegerField('接口数量', default=1)
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cal_a2l_module_parameter'
        verbose_name = 'A2L模块参数'
        unique_together = ('module', 'version_identifier', 'ecu', 'cpu_type')

    def __str__(self):
        return self.version_identifier



class Asap2Version(models.Model):
    """
    ASAP2 版本信息
    
    存储 A2L 文件中的 ASAP2 版本号和升级号信息
    
    对应 A2L 文件中的：
    ASAP2_VERSION 1 61
    """
    
    a2l_file = models.OneToOneField(
        A2LFile, 
        verbose_name='关联A2L文件', 
        on_delete=models.DO_NOTHING, 
        related_name='asap2_versions', 
        db_constraint=True)
    
    version_no = models.PositiveSmallIntegerField(
        '版本号', 
        help_text='ASAP2 主版本号，如 1'
    )
    upgrade_no = models.PositiveSmallIntegerField(
        '升级号', 
        help_text='ASAP2 升级版本号，如 71'
    )
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)
    
    # # 元数据
    # created_at = models.DateTimeField('创建时间', auto_now_add=True)
    # updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'cal_asap2_version'
        verbose_name = 'ASAP2版本信息'
        verbose_name_plural = 'ASAP2版本信息'
        unique_together = ('a2l_file', 'version_no', 'upgrade_no')  # 每个A2L文件的版本组合唯一
        
    def __str__(self):
        return f"{self.a2l_file.name if self.a2l_file else 'Unknown'}: v{self.version_no}.{self.upgrade_no}"
    
    @property
    def full_version(self):
        """返回完整版本字符串"""
        return f"{self.version_no}.{self.upgrade_no}"


class CompuMethod(models.Model):
    """
    从 A2L 文件解析的转换方法定义（COMPU_METHOD 块）
    
    用于定义如何将原始数据转换为物理值，支持线性转换、查表转换等。
    
    /begin COMPU_METHOD
    /* Name of CompuMethod    */      BMS_EstCPC_CM_uint16
    /* Long identifier        */      "Q = V"
    /* Conversion Type        */      RAT_FUNC
    /* Format                 */      "%5.0"
    /* Units                  */      ""
    /* Coefficients           */      COEFFS 0 1 0 0 0 1
    /end COMPU_METHOD
    
    对应字段：
    - name: 转换方法名称
    - long_identifier: 长标识符/描述
    - conversion_type: 转换类型
    - format: 格式化字符串
    - units: 单位
    - coefficients: 转换系数
    """
    
    CONVERSION_TYPE_CHOICES = [
        ('IDENTICAL', '恒等转换'),
        ('LINEAR', '线性转换'),
        ('RAT_FUNC', '有理函数转换'),
        ('TAB_INTP', '插值查表'),
        ('TAB_NOINTP', '非插值查表'),
        ('TAB_VERB', '文本查表'),
        ('FORM', '公式转换'),
    ]
    
    a2l_file = models.ForeignKey(
        A2LFile, 
        verbose_name='关联A2L文件', 
        on_delete=models.DO_NOTHING, 
        related_name='a2l_compu_methods', 
        db_constraint=False)
    
    # 基本信息
    name = models.CharField('转换方法名称', max_length=64)
    long_identifier = models.TextField('长标识符', blank=True, default='')
    conversion_type = models.CharField('转换类型', max_length=32, choices=CONVERSION_TYPE_CHOICES, default='RAT_FUNC')
    format_str = models.CharField('格式化字符串', max_length=32, blank=True, default='')
    units = models.CharField('单位', max_length=32, blank=True, default='')
    
    # 转换参数, 
    # todo 后续可以考虑转换为coeffs类，将每个参数转换为abcdef六个参数，关联到此模型
    # coefficients = models.CharField('转换系数', max_length=128, blank=True, default='', help_text='存储转换公式的系数，如：0 1 0 0 0 1')
    # 经实践分析发现 很多方法的的Coefficients都是同一个，所以使用外键关联到Coeffs模型
    coefficient = models.ForeignKey(
        'Coeffs',  # 可以用字符串延迟引用
        on_delete=models.DO_NOTHING,
        related_name='coefficient_compu_method',
        verbose_name="换算方法",
        db_constraint=False
    )
    
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'cal_compu_method'
        verbose_name = '转换方法定义'
        # unique_together = ('a2l_file', 'name')  # 每个 A2L 文件内名称唯一
        
    def __str__(self):
        return f"{self.a2l_file.name if self.a2l_file else 'Unknown'}:{self.name}"


class Coeffs(models.Model):
    """
    # 此模型用于存储与换算方法（CompuMethod）相关的六个换算系数（a, b, c, d, e, f）。
    # 这些系数通常用于将原始ECU数据通过线性或有理函数等公式转换为物理值。
    # 每组系数与一个换算方法一一关联（OneToOne），便于复用和高效查询。
    # CompuMethod 换算关系对应的完整公式如下：
    #
    # 通常使用六个系数 (a, b, c, d, e, f) 实现原始值 <=> 物理值的双向变换。有理函数（RAT_FUNC）常见如下形式：
    #
    #   物理值 = (a * 原始值^2 + b * 原始值 + c) / (d * 原始值^2 + e * 原始值 + f)
    #
    # 反向转换（物理值 -> 原始值）通常需要单独计算，具体形式取决于系数和实际场景。
    #
    # 线性换算（LINEAR/IDENTICAL）为特殊情况：
    #   物理值 = b * 原始值 + c
    #   其中 a=d=e=0, f=1 只需用 b, c
    #
    # 例如在 A2L COEFFS 表达式中的顺序为 a b c d e f。
    #
    # 实际公式及求值时请以 specific CompuMethod 类型和A2L定义为准，如有需要可实现物理值与原始值的双向换算方法。
    """
    # 系数a, b, c, d, e, f 的默认值为0, 1, 0, 0, 0, 1, 方便线性换算和有理函数换算的默认值,即 Q=V
    a = models.FloatField(verbose_name="系数a", default=0)
    b = models.FloatField(verbose_name="系数b", default=1)
    c = models.FloatField(verbose_name="系数c", default=0)
    d = models.FloatField(verbose_name="系数d", default=0)
    e = models.FloatField(verbose_name="系数e", default=0)
    f = models.FloatField(verbose_name="系数f", default=1)
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)


    class Meta:
        db_table = 'cal_coeffs'  # 对应 SQLAlchemy 的 __tablename__
        verbose_name = "换算系数"
        verbose_name_plural = "换算系数"
        unique_together = ('a', 'b', 'c', 'd', 'e', 'f')

    def __str__(self):
        return f"Coeffs(a={self.a}, b={self.b}, c={self.c}, d={self.d}, e={self.e}, f={self.f})"


class Maturity(models.Model):
    """成熟度选项（单表）：包含模板名称、说明与数值(0~1)。

    多条记录共享同一 name 代表同一模板下的多个成熟度选项。
    例如：name="默认模板"，value ∈ {0、0.4、0.5、0.65、0.8、0.99、1.0}
    """

    name = models.CharField('模板名称', max_length=64, unique=True)
    description = models.TextField('模板说明', blank=True, default='')
    value = models.DecimalField('成熟度(0~1)', max_digits=4, decimal_places=3, validators=[MinValueValidator(0), MaxValueValidator(1)])
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cal_maturity'
        verbose_name = '成熟度'

    def __str__(self):
        try:
            return f"{self.name}:{float(self.value) * 100:.0f}%"
        except Exception:
            return f"{self.name}:{self.value}"




class Characteristic(models.Model):
    """从A2L文件解析的标定参数定义（CHARACTERISTIC块）修改 ECU 参数
    标定量
    备注：常规A2L文件通常包含几百到几千个CHARACTERISTIC块，每个块包含几个到几十个参数。

    参数标准定义：
    /begin CHARACTERISTIC
    /* Name                   */      ACChrgOILvl1_FltCriPrm
    /* Long Identifier        */      "DiagSys_st_ChrgOILvl1 Fault Criteria"
    /* Type                   */      VALUE
    /* ECU Address            */      -0xfdc55c48 /* @ECU_Address@ACChrgOILvl1_FltCriPrm@ */
    /* Record Layout          */      Scalar_FLOAT32_IEEE
    /* Maximum Difference     */      0
    /* Conversion Method      */      BMS_CtrlACC_CM_single
    /* Lower Limit            */      -3.4E+38
    /* Upper Limit            */      3.4E+38
    /* Number                 */      280

    /end CHARACTERISTIC
        
    对应的字段名称为：
    name: 标定参数名称
    long_identifier: 标定参数长标识符
    param_type: 标定参数类型
    ecu_address: ECU地址，用以寻址最终的值
    record_layout: 记录布局，用以确定读取的字节数
    conversion_method: 转换方法，用以将读取的字节转换为最终的值
    max_diff: 最大差值
    lower_limit: 下限
    upper_limit: 上限
    
    """
    CHARACTERISTIC_TYPE_CHOICES = (
        ('VALUE', '标量值'),
        ('VAL_BLK', '值块'),
        ('MAP', '映射表'),
        ('CURVE', '曲线'),
    )
    
    RECORD_LAYOUT_CHOICES = [
        # === 标量类型 ===
        ('Scalar_UBYTE', '8位无符号整数'),
        ('Scalar_SBYTE', '8位有符号整数'),
        ('Scalar_UWORD', '16位无符号整数'),
        ('Scalar_SWORD', '16位有符号整数'),
        ('Scalar_ULONG', '32位无符号整数'),
        ('Scalar_SLONG', '32位有符号整数'),
        ('Scalar_FLOAT32_IEEE', '32位浮点数'),
        ('Scalar_FLOAT64_IEEE', '64位浮点数'),

        # === 数组类型 ===
        ('Array_UBYTE', '8位无符号整数数组'),
        ('Array_SBYTE', '8位有符号整数数组'),
        ('Array_UWORD', '16位无符号整数数组'),
        ('Array_SWORD', '16位有符号整数数组'),
        ('Array_ULONG', '32位无符号整数数组'),
        ('Array_SLONG', '32位有符号整数数组'),
        ('Array_FLOAT32_IEEE', '32位浮点数组'),
        ('Array_FLOAT64_IEEE', '64位浮点数组'),

        # === 映射表类型（MAP / CURVE类）===
        ('Map_FLOAT32_IEEE', '32位浮点映射表'),
        ('Map_UBYTE', '8位无符号整数映射表'),
        ('Map_SBYTE', '8位有符号整数映射表'),
        ('Map_UWORD', '16位无符号整数映射表'),
        ('Map_SWORD', '16位有符号整数映射表'),
    ]

    
    a2l_file = models.ForeignKey(
        A2LFile, 
        verbose_name='关联A2L文件', 
        on_delete=models.DO_NOTHING, 
        related_name='a2l_characteristics', 
        db_constraint=False)

    work_package = models.ForeignKey(
          WorkPackage,
          verbose_name='关联标定工作包', 
          on_delete=models.DO_NOTHING,
          related_name='wp_characteristics', 
          db_constraint=False,
          default=get_default_unbound_work_package  # 默认使用"未绑定"工作包，后续可手工绑定
      )

    module = models.ForeignKey(
        A2LModule,
        verbose_name='所属模块',
        on_delete=models.DO_NOTHING,
        related_name='module_characteristics',
        db_constraint=False
    )
    
    # 基本信息
    name = models.CharField('参数名称', max_length=128)
    long_identifier = models.TextField('长标识符', blank=True, default='')
    characteristic_type = models.CharField('参数类型', max_length=32, choices=CHARACTERISTIC_TYPE_CHOICES, default='VALUE')
    ecu_address = models.BigIntegerField()
    record_layout = models.CharField('记录布局', max_length=64, choices=RECORD_LAYOUT_CHOICES)
    conversion_method = models.ForeignKey(
        CompuMethod,
        verbose_name='转换方法',
        on_delete=models.DO_NOTHING,
        related_name='conversion_characteristics',
        db_constraint=False
    )
    
    # 范围与标定相关属性
    max_diff = models.FloatField('最大差值', blank=True, default=0.0)
    lower_limit = models.FloatField('下限')  # 用户约定不以该值为判断，仅可读，避免干扰原始值
    upper_limit = models.FloatField('上限')  # 用户约定不以该值为判断，仅可读，避免干扰原始值

    # 非 VALUE 类型（如 MAP/CURVE/VAL_BLK）中使用的点数定义；VALUE 类型默认无此字段
    number = models.PositiveIntegerField('个数', default=0, help_text='仅非VALUE类型有效')

     # 标定工作包维度
    is_key = models.BooleanField('是否为关键标定量', default=False)
    edit_upper_limit = models.FloatField('标定量填写范围上限', default=-1, help_text='默认-1表示尚未填写')
    edit_lower_limit = models.FloatField('标定量填写范围下限', default=-1, help_text='默认-1表示尚未填写')
    related_test_cases = models.CharField('标定的相关测试用例', max_length=256, default='')
    pass_standard = models.CharField('标定涉及通过标准', max_length=256, default='')
    remark = models.TextField('备注', blank=True, default='')


    # 可选备注/标注,预估每1万行占用0.5M空间
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)
    # creator = models.CharField('创建人', max_length=64)
    updater = models.CharField('更新人', max_length=64)


    class Meta:
        db_table = 'cal_characteristic'
        verbose_name = '标定参数定义'
        unique_together = ('a2l_file', 'name', 'ecu_address', 'conversion_method')  # 基于 A2L 文件、名称、ECU 地址和转换方法的唯一性

    def __str__(self):
        return f"{self.name}"



# todo 目前业务暂时未使用，未来可能需要扩展
class Measurement(models.Model):
    """
    从 A2L 文件解析的测量量定义（MEASUREMENT 块）采集 ECU 实时信号

    begin MEASUREMENT
    /* Name                   */      PosRlyAgiFlt_init
    /* Long identifier        */      ""
    /* Data type              */      UBYTE
    /* Conversion method      */      BMS_DiagSys_CM_uint8
    /* Resolution (Not used)  */      0
    /* Accuracy (Not used)    */      0
    /* Lower limit            */      0
    /* Upper limit            */      255
    ECU_ADDRESS                       0xfe43bd77 /* @ECU_Address@PosRlyAgiFlt_init@ */
    /end MEASUREMENT

    对应字段：
    - name: 测量量名称
    - long_identifier: 测量量长标识符
    - datatype: 数据类型
    - conversion_method: 转换方法
    - resolution: 分辨率
    - accuracy: 精度
    - lower_limit: 下限
    - upper_limit: 上限

    可选关联元素：
    - Annotation, ArraySize, BitMask, BitOperation, ByteOrder, Discrete,
      DisplayIdentifier, EcuAddress, EcuAddressExtension, ErrorMask, Format,
      FunctionList, IfData, Layout, MatrixDim, MaxRefresh, PhysUnit, ReadWrite,
      RefMemorySegment, SymbolLink, Virtual
    """
    
    DATATYPE_CHOICES = [
        # === 基本数据类型 ===
        ('UBYTE', '8位无符号整数'),
        ('SBYTE', '8位有符号整数'),
        ('UWORD', '16位无符号整数'),
        ('SWORD', '16位有符号整数'),
        ('ULONG', '32位无符号整数'),
        ('SLONG', '32位有符号整数'),
        ('FLOAT32_IEEE', '32位浮点数'),
        ('FLOAT64_IEEE', '64位浮点数'),
        
        # === 特殊类型 ===
        ('A_UINT8', '8位无符号整数数组'),
        ('A_INT8', '8位有符号整数数组'),
        ('A_UINT16', '16位无符号整数数组'),
        ('A_INT16', '16位有符号整数数组'),
        ('A_UINT32', '32位无符号整数数组'),
        ('A_INT32', '32位有符号整数数组'),
        ('A_FLOAT32_IEEE', '32位浮点数数组'),
        ('A_FLOAT64_IEEE', '64位浮点数数组'),
    ]

    a2l_file = models.ForeignKey(
        A2LFile, 
        verbose_name='关联A2L文件', 
        on_delete=models.DO_NOTHING, 
        related_name='a2l_measurements', 
        db_constraint=False)

    work_package = models.ForeignKey(
          WorkPackage,
          verbose_name='关联标定工作包', 
          on_delete=models.DO_NOTHING,
          related_name='wp_measurements', 
          db_constraint=False,
          default=get_default_unbound_work_package  # 默认使用"未绑定"工作包，后续可手工绑定
      )
    
    module = models.ForeignKey(
        A2LModule,
        verbose_name='所属模块',
        on_delete=models.DO_NOTHING,
        related_name='module_measurements',
        db_constraint=False
    )

    # 基本信息
    name = models.CharField('参数名称', max_length=128)
    long_identifier = models.TextField('长标识符', blank=True, default='')
    datatype = models.CharField('数据类型', max_length=32, choices=DATATYPE_CHOICES)
    conversion_method = models.ForeignKey(
        CompuMethod,
        verbose_name='转换方法',
        on_delete=models.DO_NOTHING,
        related_name='conversion_measurements',
        db_constraint=False
    )
    resolution = models.PositiveIntegerField('分辨率', blank=True, default=0)
    accuracy = models.FloatField('精度', blank=True, default=0.0)
    lower_limit = models.FloatField('下限')
    upper_limit = models.FloatField('上限')
    ecu_address = models.BigIntegerField()
    metadata = models.JSONField(
        verbose_name='元数据', default=dict, help_text='记录原始数据')

    # 可选备注/标注
    remark = models.TextField('备注', blank=True, default='')
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)
    # 冗余字段，供后续扩展使用
    # creator = models.CharField('创建人', max_length=64)
    # updater = models.CharField('更新人', max_length=64)

    class Meta:
        db_table = 'cal_measurement'
        verbose_name = '测量量定义'
        unique_together = ('a2l_file', 'name', 'ecu_address', 'conversion_method') 

    def __str__(self):
        return self.name


class Hex(models.Model):
    """按 Intel HEX 行结构存储解析结果，并关联到参数的最终值
    
    
    参考：https://xiaopeng.feishu.cn/wiki/UgWRw1jEpinxCEkZPjJcDt4DnDd

    备注：由于Hex文件很大，通常20w行以上，

    详细格式:
    Intel HEX 行格式: :llaaaatt[dd...]cc
    - ll: 本行数据字节数（十六进制）
    - aaaa: 本行数据的偏移地址（相对当前段/线性基地址）
    - tt: 记录类型（00 数据，01 文件结束，04 段地址扩展，05 线性地址扩展 等）
    - dd: 实际数据内容
    - cc: 校验和

    示例:
    :200020002000E62F2000E0172008E01F200800E21FE824065004E0FD25065094E0FD2306CC
    解释:
    - ll: 20 表示本行数据字节数为 32 字节（十六进制）
    - aaaa: 0020 表示本行数据的偏移地址为 8192（十进制）
    - tt: 00 表示记录类型为数据(00 数据，01 文件结束，04 段地址扩展，05 线性地址扩展 等)
    - dd: 200020002000E62F2000E0172008E01F200800E21FE824065004E0FD25065094E0FD2306 表示实际数据内容
    - cc: CC 表示校验和为 204（十六进制）
    """

    hex_file = models.ForeignKey(
        DataFile,
        verbose_name='关联HEX数据文件',
        on_delete=models.DO_NOTHING, 
        related_name='hex_values', 
        db_constraint=False)

    characteristic = models.ForeignKey(
        Characteristic, 
        verbose_name='关联标定参数',
        on_delete=models.DO_NOTHING, 
        related_name='characteristic_values',
        db_constraint=False)

    # 抽象后的成熟度等级（引用到 Maturity 单表项）
    maturity = models.ForeignKey(
        Maturity,
        on_delete=models.DO_NOTHING,
        related_name='maturity_values',
        verbose_name='成熟度',
        db_constraint=False
    )
    # 行级追溯信息
    line_no = models.PositiveIntegerField('行号', default=0)

    byte_count = models.PositiveSmallIntegerField('ll 本行字节数', default=0)
    offset_addr = models.PositiveIntegerField('aaaa 偏移地址', default=0)
    record_type = models.PositiveSmallIntegerField('tt 记录类型', default=0)
    data_bytes = models.BinaryField('数据dd（二进制）', blank=True, default=b'')
    checksum = models.PositiveSmallIntegerField('cc 校验和', default=0)

    # 当前标定值：统一存为数组形式，VALUE类型: [123.45] ，MAP/CURVE/VAL_BLK: [0, 400, 600, 800]
    #   VALUE类型: [123.45]  # 单个值存为长度为1的数组
    #   MAP/CURVE/VAL_BLK: [0, 400, 600, 800]  # 多维数据存为数组
    current_value = models.JSONField('当前标定值', blank=True, default=list, help_text='统一存为数组：VALUE类型[value]，MAP/CURVE存多元素数组')
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cal_hex'
        verbose_name = 'HEX数据记录'
        unique_together = ('hex_file', 'characteristic', 'line_no')


class AxisPts(models.Model):
    """轴点定义（AXIS_PTS）：用于 MAP 和 CURVE 类型的轴数据点
    
    A2L 示例：
    /begin AXIS_PTS
    /* Name                   */      CtrlACC_degC_PrvtOverChrgLookupT_X
    /* Long Identifier        */      "充电末端防超充温度-电压查表温度值"
    /* ECU Address            */      0x46600 /* @ECU_Address@CtrlACC_degC_PrvtOverChrgLookupT_X@ */
    /* Input Quantity         */      NO_INPUT_QUANTITY
    /* Record Layout          */      Lookup1D_X_FLOAT32_IEEE
    /* Maximum Difference     */      0
    /* Conversion Method      */      BMS_CtrlACC_CM_single
    /* Number of Axis Pts     */      18
    /* Lower Limit            */      -3.4E+38
    /* Upper Limit            */      3.4E+38
    /end AXIS_PTS
    """
    
    a2l_file = models.ForeignKey(
        A2LFile,
        verbose_name='关联A2L文件',
        on_delete=models.DO_NOTHING,
        related_name='a2l_axis_pts',
        db_constraint=False
    )
    module = models.ForeignKey(
        A2LModule,
        verbose_name='所属模块',
        on_delete=models.DO_NOTHING,
        related_name='module_axis_pts',
        db_constraint=False
    )
    
    name = models.CharField('轴点名称', max_length=64)
    long_identifier = models.TextField('长标识符', blank=True, default='')
    address = models.BigIntegerField('ECU地址')
    input_quantity = models.CharField('输入量', max_length=64, blank=True, default='')
    record_layout = models.CharField('存储属性', max_length=64, blank=True, default='')
    max_diff = models.FloatField('最大差值', default=0.0)
    conversion_method = models.CharField('转换方法名', max_length=64, blank=True, default='')
    max_axis_points = models.PositiveIntegerField('最大轴点数', default=0)
    lower_limit = models.FloatField('下限')
    upper_limit = models.FloatField('上限')
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'cal_axis_pts'
        verbose_name = '轴点定义'
        unique_together = ('a2l_file', 'name', 'address', 'conversion_method')
    
    def __str__(self):
        return f"{self.name}"


class AxisDescr(models.Model):
    """轴描述（AXIS_DESCR）：定义 MAP/CURVE 的坐标轴属性
    
    用于 MAP 和 CURVE 类型，描述 X/Y 轴或多轴的配置
    """
    
    characteristic = models.ForeignKey(
        'Characteristic',
        verbose_name='关联标定参数',
        on_delete=models.DO_NOTHING,
        related_name='characteristic_axis_descrs',
        db_constraint=False
    )
    
    attribute = models.CharField('轴属性', max_length=32, blank=True, default='')
    input_quantity = models.CharField('输入量', max_length=64, blank=True, default='')
    conversion_method = models.CharField('转换方法名', max_length=64, blank=True, default='')
    max_axis_points = models.PositiveIntegerField('最大轴点数', default=0)
    lower_limit = models.FloatField('下限')
    upper_limit = models.FloatField('上限')
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'cal_axis_descr'
        verbose_name = '轴描述'
    
    def __str__(self):
        return f"{self.attribute}"


class AxisPtsRef(models.Model):
    """轴点引用（AXIS_PTS_REF）：将轴描述关联到具体的轴点"""
    
    axis_descr = models.ForeignKey(
        AxisDescr,
        verbose_name='关联轴描述',
        on_delete=models.DO_NOTHING,
        related_name='axis_pts_ref',
        db_constraint=False
    )
    
    axis_points = models.CharField('轴点引用名', max_length=64)
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'cal_axis_pts_ref'
        verbose_name = '轴点引用'
        # unique_together = ('axis_descr', 'axis_points')
    
    def __str__(self):
        return f"{self.axis_points}"


class RecordLayout(models.Model):
    """记录布局（RECORD_LAYOUT）"""

    module = models.ForeignKey(
        A2LModule,
        verbose_name='所属模块',
        on_delete=models.DO_NOTHING,
        related_name='record_layouts',
        db_constraint=False
    )

    name = models.CharField('名称', max_length=128)
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cal_record_layout'
        verbose_name = 'A2L记录布局'
        # unique_together = ('module', 'name')

    def __str__(self):
        return self.name


class AxisPtsX(models.Model):
    """记录布局子项：AXIS_PTS_X"""

    record_layout = models.ForeignKey(
        RecordLayout,
        verbose_name='关联记录布局',
        on_delete=models.DO_NOTHING,
        related_name='axis_pts_x',
        db_constraint=False
    )

    position = models.PositiveIntegerField('position')
    datatype = models.CharField('datatype', max_length=64)
    index_incr = models.CharField('indexIncr', max_length=32)
    addressing = models.CharField('addressing', max_length=32)
    create_time = models.DateTimeField('记录时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cal_axis_pts_x'
        verbose_name = 'A2L记录布局-AXIS_PTS_X'
        # unique_together = ('record_layout', 'position','datatype')