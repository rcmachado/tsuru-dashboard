import tarfile
import datetime
import calendar
from cStringIO import StringIO
from zipfile import ZipFile
from base64 import decodestring

import requests

from django.template.response import TemplateResponse
from django.http import HttpResponse, HttpResponseServerError, Http404, StreamingHttpResponse, JsonResponse
from django.shortcuts import redirect
from django.core.urlresolvers import reverse
from django.views.generic import TemplateView
from django.contrib import messages

from pygments import highlight
from pygments.lexers import DiffLexer
from pygments.formatters import HtmlFormatter

from tsuru_dashboard import settings
from tsuru_dashboard.auth.views import LoginRequiredView, LoginRequiredMixin

from .forms import AppForm


class AppMixin(LoginRequiredMixin):
    def get_app(self, app_name):
        url = '{}/apps/{}'.format(settings.TSURU_HOST, app_name)
        response = requests.get(url, headers=self.authorization)

        if response.status_code == 404:
            raise Http404()

        return response.json()

    def get_context_data(self, *args, **kwargs):
        context = super(AppMixin, self).get_context_data(*args, **kwargs)

        app_name = kwargs['app_name']
        context['app'] = self.get_app(app_name)
        return context


class DeployInfo(AppMixin, TemplateView):
    template_name = 'apps/deploy.html'

    def get_context_data(self, *args, **kwargs):
        deploy_id = kwargs['deploy']
        context = super(DeployInfo, self).get_context_data(*args, **kwargs)

        url = '{}/deploys/{}'.format(settings.TSURU_HOST, deploy_id)
        response = requests.get(url, headers=self.authorization)
        context['deploy'] = response.json()

        diff = context['deploy'].get('Diff')
        if diff and diff != u'The deployment must have at least two commits for the diff.':
            format = HtmlFormatter()
            diff = highlight(diff, DiffLexer(), format)
        else:
            diff = None

        context['deploy']['Diff'] = diff
        return context


class ListDeploy(LoginRequiredView):
    template = 'apps/deploys.html'

    def zip_to_targz(self, zip_file):
        fd = StringIO()
        tar = tarfile.open(fileobj=fd, mode='w:gz')
        timeshift = int((datetime.datetime.now() - datetime.datetime.utcnow()).total_seconds())

        with ZipFile(zip_file) as f:
            for zip_info in f.infolist():
                tar_info = tarfile.TarInfo(name=zip_info.filename)
                tar_info.size = zip_info.file_size
                tar_info.mtime = calendar.timegm(zip_info.date_time) - timeshift

                if zip_info.filename.endswith("/"):
                    tar_info.mode = 0755
                    tar_info.type = tarfile.DIRTYPE
                else:
                    tar_info.mode = 0644
                    tar_info.type = tarfile.REGTYPE

                tar.addfile(tar_info, f.open(zip_info.filename))

        tar.close()
        fd.seek(0)
        return fd

    def read_zip(self, request):
        fd = StringIO()
        fd.write(decodestring(request.POST['filecontent']))
        fd.seek(0)
        return fd

    def deploy(self, request, app_name, tar_file):
        def sending_stream():
            origin = 'drag-and-drop'
            url = '{}/apps/{}/deploy?origin={}'.format(settings.TSURU_HOST, app_name, origin)
            r = requests.post(url, headers=self.authorization, files={'file': tar_file}, stream=True)
            for line in r.iter_lines():
                yield "{}<br>".format(line)
        return StreamingHttpResponse(sending_stream())

    def post(self, request, *args, **kwargs):
        app_name = kwargs['app_name']
        zip_file = self.read_zip(request)
        tar_file = self.zip_to_targz(zip_file)
        return self.deploy(request, app_name, tar_file)

    def get_app(self, app_name):
        url = '{}/apps/{}'.format(settings.TSURU_HOST, app_name)
        return requests.get(url, headers=self.authorization).json()

    def get(self, request, *args, **kwargs):
        app_name = kwargs['app_name']

        page = int(request.GET.get('page', '1'))

        skip = (page * 20) - 20
        limit = page * 20

        url = '{}/deploys?app={}&skip={}&limit={}'.format(
            settings.TSURU_HOST, app_name, skip, limit)
        response = requests.get(url, headers=self.authorization)

        deploys = []
        if response.status_code != 204:
            deploys = response.json() or []

        context = {}
        context['deploys'] = deploys
        context['app'] = self.get_app(app_name)

        if len(deploys) >= 20:
            context['next'] = page + 1

        if page > 0:
            context['previous'] = page - 1

        return TemplateResponse(request, self.template, context=context)


class AppDetail(AppMixin, TemplateView):
    template_name = 'apps/details.html'

    def service_instances(self, app_name):
        tsuru_url = '{}/services/instances?app={}'.format(settings.TSURU_HOST, app_name)
        response = requests.get(tsuru_url, headers=self.authorization)
        if response.status_code == 200:
            return response.json()
        return []

    def get_context_data(self, *args, **kwargs):
        context = super(AppDetail, self).get_context_data(*args, **kwargs)
        app_name = kwargs['app_name']
        service_instances = []

        for service in self.service_instances(app_name):
            if service['instances']:
                service_instances.append(
                    {'name': service['instances'][0], 'servicename': service['service']}
                )

        context['app']['service_instances'] = service_instances
        return context


class CreateApp(LoginRequiredView):
    template_name = 'apps/create.html'

    def render(self, request, context):
        return TemplateResponse(request, self.template_name, context)

    def get(self, request):
        form = AppForm()
        default, plans = self.plans(request)
        form.fields['plan'].choices = plans
        form.fields['plan'].initial = default
        form.fields['platform'].choices = self.platforms(request)
        form.fields['teamOwner'].choices = self.teams(request)
        form.fields['pool'].choices = self.pools(request)
        context = {
            'app_form': form,
        }
        return self.render(request, context)

    def post(self, request):
        context = {}
        form = AppForm(request.POST)
        default, plans = self.plans(request)
        form.fields['plan'].choices = plans
        form.fields['platform'].choices = self.platforms(request)
        form.fields['teamOwner'].choices = self.teams(request)
        form.fields['pool'].choices = self.pools(request)
        if form.is_valid():
            authorization = {'authorization': request.session.get('tsuru_token')}

            # removing keys with empty values
            data = {key: value for key, value in form.cleaned_data.items() if value}

            url = '{}/apps'.format(settings.TSURU_HOST)
            response = requests.post(url, data=data, headers=authorization)

            if response.status_code == 201:
                messages.success(request, u'App was successfully created', fail_silently=True)
                return redirect(reverse('list-app'))

            messages.error(request, response.content, fail_silently=True)

        form.fields['plan'].initial = default
        context['app_form'] = form
        return self.render(request, context)

    def pools(self, request):
        authorization = {'authorization': request.session.get('tsuru_token')}
        url = '{}/pools'.format(settings.TSURU_HOST)
        response = requests.get(url, headers=authorization)
        pools = set()
        pools_json = response.json()
        pools_by_team = pools_json

        # backward compatibility
        if isinstance(pools_json, dict):
            pools_by_team = pools_json["pools_by_team"]
            for pool in pools_json.get('public_pools', []):
                pools.add(pool.get("Name", pool))

            for team_list in pools_by_team:
                for pool in team_list['Pools']:
                    pools.add(pool)

        if isinstance(pools_json, list):
            for pool in pools_json:
                # backward compatibility
                if "Pools" in pool:
                    pools.update(pool.get("Pools"))

                if "Name" in pool:
                    pools.add(pool.get("Name"))

        result = [('', '')]
        result.extend([(p, p) for p in pools])
        return result

    def teams(self, request):
        authorization = {'authorization': request.session.get('tsuru_token')}
        url = '{}/teams'.format(settings.TSURU_HOST)
        result = [("", "")]
        response = requests.get(url, headers=authorization)
        if response.status_code != 204:
            teams = response.json()
            result.extend([(t['name'], t['name']) for t in teams])
        return result

    def platforms(self, request):
        authorization = {'authorization': request.session.get('tsuru_token')}
        response = requests.get('{}/platforms'.format(settings.TSURU_HOST), headers=authorization)
        platforms = response.json()
        result = [(', ')]
        result.extend([(p['Name'], p['Name']) for p in platforms])
        return result

    def plans(self, request):
        authorization = {'authorization': request.session.get('tsuru_token')}
        url = '{}/plans'.format(settings.TSURU_HOST)
        response = requests.get(url, headers=authorization)
        plans = []
        if response.status_code == 200:
            plans = response.json()
        plan_list = [('', '')]
        default = ''
        for p in plans:
            if p.get('default'):
                default = p['name']
            plan_list.append((p['name'], p['name']))
        return default, plan_list


class RemoveApp(LoginRequiredView):
    def get(self, request, *args, **kwargs):
        app_name = self.kwargs['name']
        authorization = {'authorization': request.session.get('tsuru_token')}
        response = requests.delete(
            '{}/apps/{}'.format(settings.TSURU_HOST, app_name),
            headers=authorization
        )
        if response.status_code > 399:
            return HttpResponse(response.text, status=response.status_code)
        return redirect(reverse('list-app'))


class ListApp(LoginRequiredMixin, TemplateView):
    template_name = "apps/list.html"


class ListAppJson(LoginRequiredView):
    def list_apps(self, name=None):
        url = "{}/apps".format(settings.TSURU_HOST)

        if name:
            url = "{}?name={}".format(url, name)

        response = requests.get(url, headers=self.authorization)

        apps = []
        if response.status_code != 204:
            apps = sorted(response.json(), key=lambda item: item['name'])

        return apps

    def get(self, *args, **kwargs):
        app_list = {"apps": self.list_apps(self.request.GET.get("name"))}
        return JsonResponse(app_list, safe=False)


class AppDetailJson(LoginRequiredView):

    def get_containers(self, app_name):
        url = '{}/docker/node/apps/{}/containers'.format(settings.TSURU_HOST, app_name)
        response = requests.get(url, headers=self.authorization)

        if response.status_code != 200:
            return []

        data = response.json()
        if not data:
            return []

        return data

    def get(self, *args, **kwargs):
        context = {}
        app_name = kwargs['app_name']
        token = self.request.session.get('tsuru_token')
        url = '{}/apps/{}'.format(settings.TSURU_HOST, app_name)
        headers = {
            'content-type': 'application/json',
            'Authorization': token,
        }

        response = requests.get(url, headers=headers)
        if response.status_code == 404:
            raise Http404()

        context['app'] = response.json()

        units_by_status = {}
        for unit in context['app']['units']:
            if unit['Status'] not in units_by_status:
                units_by_status[unit['Status']] = [unit]
            else:
                units_by_status[unit['Status']].append(unit)

        for container in self.get_containers(app_name):
            for index, unit in enumerate(context['app']['units']):
                if self.id_or_name(unit) == container['ID']:
                    context['app']['units'][index].update({
                        'HostAddr': container['HostAddr'],
                        'HostPort': container['HostPort'],
                    })
        context['units_by_status'] = units_by_status
        context['process_list'] = self.process_list(context['app'])
        return JsonResponse(context, safe=False)

    def process_list(self, app):
        process = set()

        for unit in app.get('units', []):
            if 'ProcessName' in unit:
                process.add(unit['ProcessName'])

        return list(process)

    def id_or_name(self, unit):
        if "ID" in unit:
            return unit["ID"]
        return unit["Name"]


class LogStream(LoginRequiredView):
    def get(self, request, *args, **kwargs):
        app_name = kwargs['app_name']

        def sending_stream():
            url = '{}/apps/{}/log?lines=15&follow=1'.format(settings.TSURU_HOST, app_name)
            r = requests.get(url, headers=self.authorization, stream=True)
            for line in r.iter_lines():
                yield line

        return StreamingHttpResponse(sending_stream())


class AppLog(AppMixin, TemplateView):
    template_name = 'apps/app_log.html'


class AppRollback(LoginRequiredView):
    def get(self, request, app_name, image):
        origin = "rollback"
        url = '{}/apps/{}/deploy/rollback?origin={}'.format(settings.TSURU_HOST, app_name, origin)
        response = requests.post(url, headers=self.authorization, data={'image': image})
        if response.status_code == 200:
            return redirect(reverse('app-deploys', args=[app_name]))
        return HttpResponseServerError('NOT OK')


class Settings(AppMixin, TemplateView):
    template_name = 'apps/settings.html'

    def get_envs(self, app_name):
        url = '{}/apps/{}/env'.format(settings.TSURU_HOST, app_name)
        return requests.get(url, headers=self.authorization).json()

    def get_context_data(self, *args, **kwargs):
        context = super(Settings, self).get_context_data(*args, **kwargs)
        app_name = kwargs['app_name']
        context['app']['envs'] = self.get_envs(app_name)
        return context


class Unlock(LoginRequiredView):
    def get(self, request, *args, **kwargs):
        app_name = self.kwargs['name']
        response = requests.delete(
            '{}/apps/{}/lock'.format(settings.TSURU_HOST, app_name),
            headers=self.authorization
        )

        if response.status_code > 399:
            messages.error(request, response.text, fail_silently=True)
        else:
            messages.success(request, u'App was successfully unlocked', fail_silently=True)

        return redirect(reverse('app-settings', args=[app_name]))
