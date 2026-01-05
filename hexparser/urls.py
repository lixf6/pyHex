from django.urls import path
from . import views

app_name = 'hexparser'

urlpatterns = [
    path('', views.index, name='index'),
]
