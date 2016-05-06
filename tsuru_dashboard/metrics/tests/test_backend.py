from django.test import TestCase
from tsuru_dashboard.metrics.backend import AppBackend, MetricNotEnabled, ElasticSearch
from mock import patch, Mock


class AppBackendTest(TestCase):
    @patch("requests.get")
    def test_envs_from_api(self, get_mock):
        response_mock = Mock(status_code=200)
        response_mock.json.return_value = {
            "METRICS_BACKEND": "logstash",
            "METRICS_ELASTICSEARCH_HOST": "http://easearch.com",
            "METRICS_LOGSTASH_HOST": "logstash.com"
        }
        get_mock.return_value = response_mock

        app = {"name": "appname", "units": [{"ProcessName": "web"}]}

        backend = AppBackend(app, 'token')
        self.assertIsInstance(backend, ElasticSearch)

    @patch("requests.get")
    def test_envs_from_app(self, get_mock):
        get_mock.return_value = Mock(status_code=500)

        app = {"name": "appname", "envs": {"ELASTICSEARCH_HOST": "ble"}, "units": [{"ID": "id"}]}

        backend = AppBackend(app, 'token')
        self.assertIsInstance(backend, ElasticSearch)

    @patch("requests.get")
    def test_without_metrics(self, get_mock):
        get_mock.return_value = Mock(status_code=404)
        app = {"name": "appname"}

        with self.assertRaises(MetricNotEnabled):
            AppBackend(app, 'token')
