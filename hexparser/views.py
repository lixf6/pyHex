from django.shortcuts import render
from django.http import JsonResponse


def index(request):
    """首页视图"""
    return JsonResponse({
        'status': 'success',
        'message': 'HEX Parser API is running',
        'version': '1.0.0'
    })


def home(request):
    """根路径视图"""
    return JsonResponse({
        'status': 'success',
        'message': 'Welcome to pyHex - HEX Parser System',
        'version': '1.0.0',
        'endpoints': {
            'api': '/api/',
            'admin': '/admin/',
        }
    })

