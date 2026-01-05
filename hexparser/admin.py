from django.contrib import admin
from .models import (
    A2LFile,
    DataFile,
    WorkPackage,
    A2LProject,
    A2LModule,
    A2LModuleParameter,
    Asap2Version,
    CompuMethod,
    Coeffs,
    Maturity,
    Characteristic,
    Measurement,
    Hex,
    AxisPts,
    AxisDescr,
    AxisPtsRef,
    RecordLayout,
    AxisPtsX,
)


@admin.register(A2LFile)
class A2LFileAdmin(admin.ModelAdmin):
    list_display = ('name', 'file_path', 'create_time', 'update_time')
    search_fields = ('name', 'file_path')
    list_filter = ('create_time',)


@admin.register(DataFile)
class DataFileAdmin(admin.ModelAdmin):
    list_display = ('name', 'file_path', 'file_type', 'create_time')
    search_fields = ('name', 'file_path')
    list_filter = ('file_type', 'create_time')


@admin.register(WorkPackage)
class WorkPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent_id', 'owner', 'deleted', 'create_time')
    search_fields = ('name', 'owner')
    list_filter = ('deleted', 'create_time')


@admin.register(A2LProject)
class A2LProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'a2l_file', 'create_time')
    search_fields = ('name',)
    list_filter = ('create_time',)


@admin.register(A2LModule)
class A2LModuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'create_time')
    search_fields = ('name',)
    list_filter = ('create_time',)


@admin.register(Characteristic)
class CharacteristicAdmin(admin.ModelAdmin):
    list_display = ('name', 'characteristic_type', 'a2l_file', 'module', 'is_key', 'create_time')
    search_fields = ('name', 'long_identifier')
    list_filter = ('characteristic_type', 'is_key', 'create_time')


@admin.register(Measurement)
class MeasurementAdmin(admin.ModelAdmin):
    list_display = ('name', 'datatype', 'a2l_file', 'module', 'create_time')
    search_fields = ('name', 'long_identifier')
    list_filter = ('datatype', 'create_time')


@admin.register(CompuMethod)
class CompuMethodAdmin(admin.ModelAdmin):
    list_display = ('name', 'conversion_type', 'units', 'a2l_file')
    search_fields = ('name', 'long_identifier')
    list_filter = ('conversion_type',)


@admin.register(Coeffs)
class CoeffsAdmin(admin.ModelAdmin):
    list_display = ('a', 'b', 'c', 'd', 'e', 'f')


@admin.register(Maturity)
class MaturityAdmin(admin.ModelAdmin):
    list_display = ('name', 'value', 'description')
    search_fields = ('name',)


@admin.register(Hex)
class HexAdmin(admin.ModelAdmin):
    list_display = ('hex_file', 'characteristic', 'maturity', 'line_no', 'create_time')
    list_filter = ('maturity', 'create_time')


# 注册其他模型
admin.site.register(A2LModuleParameter)
admin.site.register(Asap2Version)
admin.site.register(AxisPts)
admin.site.register(AxisDescr)
admin.site.register(AxisPtsRef)
admin.site.register(RecordLayout)
admin.site.register(AxisPtsX)

