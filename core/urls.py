from django.contrib.auth.views import LogoutView
from django.urls import path
from . import views, voice_views, agent_views, realtime

urlpatterns = [
    path('login/', views.SignInView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('account/password/', views.password_change, name='password_change'),
    path('', views.dashboard, name='dashboard'),
    path('tasks/', views.task_list, name='tasks'),
    path('tasks/new/', views.task_create, name='task_create'),
    path('voice/status/', voice_views.status, name='voice_status'),
    path('voice/transcribe/', voice_views.transcribe, name='voice_transcribe'),
    path('voice/transcription-status/', voice_views.transcription_status, name='voice_transcription_status'),
    path('voice/draft/', voice_views.draft, name='voice_draft'),
    path('voice/speak/', voice_views.speak, name='voice_speak'),
    path('voice/realtime-session/', realtime.session, name='voice_realtime_session'),
    path('voice/realtime-speak/', realtime.speak, name='voice_realtime_speak'),
    path('agent/state/', agent_views.state, name='agent_state'),
    path('agent/message/', agent_views.message, name='agent_message'),
    path('agent/reset/', agent_views.reset, name='agent_reset'),
    path('tasks/<int:pk>/', views.task_detail, name='task_detail'),
    path('tasks/<int:pk>/action/', views.perform_action, name='task_action'),
    path('employees/', views.employees, name='employees'),
    path('structure/', views.structure, name='structure'),
    path('employees/new/', views.employee_edit, name='employee_create'),
    path('employees/<int:pk>/edit/', views.employee_edit, name='employee_edit'),
    path('employees/<int:pk>/toggle/', views.employee_toggle, name='employee_toggle'),
    path('employees/<int:pk>/password/', views.employee_password, name='employee_password'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/read/', views.notifications_read, name='notifications_read'),
    path('timeline/', views.timeline, name='timeline'),
    path('chains/', views.chains, name='chains'),
]
