from django.http import HttpResponse, HttpResponseBadRequest

from tsuru_dashboard import settings
from tsuru_dashboard.auth.views import LoginRequiredView
from .backend import AppBackend, ComponentBackend
import json
import requests


class Metric(LoginRequiredView):
    def get(self, *args, **kwargs):
        token = self.request.session.get('tsuru_token')
        metric = self.request.GET.get("metric")
        if not metric:
            return HttpResponseBadRequest()

        interval = self.request.GET.get("interval")
        date_range = self.request.GET.get("date_range")
        target = kwargs['target']

        backend = self.get_metrics_backend(metric=metric, target=target, date_range=date_range, token=token)
        if backend is None:
            return HttpResponseBadRequest()

        data = getattr(backend, metric)(interval=interval)
        return HttpResponse(json.dumps(data))


class AppMetric(Metric):
    def get_app(self, app_name):
        url = '{}/apps/{}'.format(settings.TSURU_HOST, app_name)
        return requests.get(url, headers=self.authorization).json()

    def get_envs(self, request, app_name):
        url = '{}/apps/{}/env'.format(settings.TSURU_HOST, app_name)
        data = requests.get(url, headers=self.authorization).json()
        envs = {}

        for env in data:
            envs[env['name']] = env['value']

        return envs

    def get_metrics_backend(self, metric, target, date_range, token):
        process_name = self.request.GET.get("process_name")
        app = self.get_app(target)
        app["envs"] = self.get_envs(self.request, target)
        return AppBackend(app=app, token=token, date_range=date_range, process_name=process_name)


class ComponentMetric(Metric):
    def get_metrics_backend(self, metric, target, date_range, token):
        return ComponentBackend(component=target, token=token, date_range=date_range)
