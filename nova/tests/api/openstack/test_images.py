# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 OpenStack LLC.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Tests of the new image services, both as a service layer,
and as a WSGI layer
"""

import copy
import json
import os
import shutil
import tempfile
import xml.dom.minidom as minidom

import mox
import stubout
import webob

from glance import client as glance_client
from nova import context
from nova import exception
from nova import test
from nova import utils
import nova.api.openstack
from nova.api.openstack import images
from nova.tests.api.openstack import fakes


class ImagesTest(test.TestCase):
    """
    Test of the OpenStack API /images application controller w/Glance.
    """
    NOW_GLANCE_FORMAT = "2010-10-11T10:30:22"
    NOW_API_FORMAT = "2010-10-11T10:30:22Z"

    def setUp(self):
        """Run before each test."""
        super(ImagesTest, self).setUp()
        self.flags(image_service='nova.image.glance.GlanceImageService')
        self.stubs = stubout.StubOutForTesting()
        fakes.stub_out_networking(self.stubs)
        fakes.stub_out_rate_limiting(self.stubs)
        fakes.stub_out_key_pair_funcs(self.stubs)
        self.fixtures = self._make_image_fixtures()
        fakes.stub_out_glance(self.stubs, initial_fixtures=self.fixtures)
        fakes.stub_out_compute_api_snapshot(self.stubs)
        fakes.stub_out_compute_api_backup(self.stubs)

    def tearDown(self):
        """Run after each test."""
        self.stubs.UnsetAll()
        super(ImagesTest, self).tearDown()

    def _get_fake_context(self):
        class Context(object):
            project_id = 'fake'
        return Context()

    def _applicable_fixture(self, fixture, user_id):
        """Determine if this fixture is applicable for given user id."""
        is_public = fixture["is_public"]
        try:
            uid = fixture["properties"]["user_id"]
        except KeyError:
            uid = None
        return uid == user_id or is_public

    def test_get_image_index(self):
        request = webob.Request.blank('/v1.0/images')
        response = request.get_response(fakes.wsgi_app())

        response_dict = json.loads(response.body)
        response_list = response_dict["images"]

        expected = [{'id': 123, 'name': 'public image'},
                    {'id': 124, 'name': 'queued snapshot'},
                    {'id': 125, 'name': 'saving snapshot'},
                    {'id': 126, 'name': 'active snapshot'},
                    {'id': 127, 'name': 'killed snapshot'},
                    {'id': 129, 'name': None}]

        self.assertDictListMatch(response_list, expected)

    def test_get_image(self):
        request = webob.Request.blank('/v1.0/images/123')
        response = request.get_response(fakes.wsgi_app())

        self.assertEqual(200, response.status_int)

        actual_image = json.loads(response.body)

        expected_image = {
            "image": {
                "id": 123,
                "name": "public image",
                "updated": self.NOW_API_FORMAT,
                "created": self.NOW_API_FORMAT,
                "status": "ACTIVE",
                "progress": 100,
            },
        }

        self.assertEqual(expected_image, actual_image)

    def test_get_image_v1_1(self):
        request = webob.Request.blank('/v1.1/fake/images/124')
        response = request.get_response(fakes.wsgi_app())

        actual_image = json.loads(response.body)

        href = "http://localhost/v1.1/fake/images/124"
        bookmark = "http://localhost/fake/images/124"
        server_href = "http://localhost/v1.1/servers/42"
        server_bookmark = "http://localhost/servers/42"

        expected_image = {
            "image": {
                "id": 124,
                "name": "queued snapshot",
                "updated": self.NOW_API_FORMAT,
                "created": self.NOW_API_FORMAT,
                "status": "QUEUED",
                "progress": 0,
                'server': {
                    'id': 42,
                    "links": [{
                        "rel": "self",
                        "href": server_href,
                    },
                    {
                        "rel": "bookmark",
                        "href": server_bookmark,
                    }],
                },
                "metadata": {
                    "instance_ref": "http://localhost/v1.1/servers/42",
                    "user_id": "fake",
                },
                "links": [{
                    "rel": "self",
                    "href": href,
                },
                {
                    "rel": "bookmark",
                    "href": bookmark,
                }],
            },
        }

        self.assertEqual(expected_image, actual_image)

    def test_get_image_xml(self):
        request = webob.Request.blank('/v1.0/images/123')
        request.accept = "application/xml"
        response = request.get_response(fakes.wsgi_app())

        actual_image = minidom.parseString(response.body.replace("  ", ""))

        expected_now = self.NOW_API_FORMAT
        expected_image = minidom.parseString("""
            <image id="123"
                    name="public image"
                    updated="%(expected_now)s"
                    created="%(expected_now)s"
                    status="ACTIVE"
                    progress="100"
                    xmlns="http://docs.rackspacecloud.com/servers/api/v1.0" />
        """ % (locals()))

        self.assertEqual(expected_image.toxml(), actual_image.toxml())

    def test_get_image_xml_no_name(self):
        request = webob.Request.blank('/v1.0/images/129')
        request.accept = "application/xml"
        response = request.get_response(fakes.wsgi_app())

        actual_image = minidom.parseString(response.body.replace("  ", ""))

        expected_now = self.NOW_API_FORMAT
        expected_image = minidom.parseString("""
            <image id="129"
                    name="None"
                    updated="%(expected_now)s"
                    created="%(expected_now)s"
                    status="ACTIVE"
                    progress="100"
                    xmlns="http://docs.rackspacecloud.com/servers/api/v1.0" />
        """ % (locals()))

        self.assertEqual(expected_image.toxml(), actual_image.toxml())

    def test_get_image_404_json(self):
        request = webob.Request.blank('/v1.0/images/NonExistantImage')
        response = request.get_response(fakes.wsgi_app())
        self.assertEqual(404, response.status_int)

        expected = {
            "itemNotFound": {
                "message": "Image not found.",
                "code": 404,
            },
        }

        actual = json.loads(response.body)

        self.assertEqual(expected, actual)

    def test_get_image_404_xml(self):
        request = webob.Request.blank('/v1.0/images/NonExistantImage')
        request.accept = "application/xml"
        response = request.get_response(fakes.wsgi_app())
        self.assertEqual(404, response.status_int)

        expected = minidom.parseString("""
            <itemNotFound code="404"
                    xmlns="http://docs.rackspacecloud.com/servers/api/v1.0">
                <message>
                    Image not found.
                </message>
            </itemNotFound>
        """.replace("  ", ""))

        actual = minidom.parseString(response.body.replace("  ", ""))

        self.assertEqual(expected.toxml(), actual.toxml())

    def test_get_image_404_v1_1_json(self):
        request = webob.Request.blank('/v1.1/fake/images/NonExistantImage')
        response = request.get_response(fakes.wsgi_app())
        self.assertEqual(404, response.status_int)

        expected = {
            "itemNotFound": {
                "message": "Image not found.",
                "code": 404,
            },
        }

        actual = json.loads(response.body)

        self.assertEqual(expected, actual)

    def test_get_image_404_v1_1_xml(self):
        request = webob.Request.blank('/v1.1/fake/images/NonExistantImage')
        request.accept = "application/xml"
        response = request.get_response(fakes.wsgi_app())
        self.assertEqual(404, response.status_int)

        # NOTE(justinsb): I believe this should still use the v1.0 XSD,
        # because the element hasn't changed definition
        expected = minidom.parseString("""
            <itemNotFound code="404"
                    xmlns="http://docs.openstack.org/compute/api/v1.1">
                <message>
                    Image not found.
                </message>
            </itemNotFound>
        """.replace("  ", ""))

        actual = minidom.parseString(response.body.replace("  ", ""))

        self.assertEqual(expected.toxml(), actual.toxml())

    def test_get_image_index_v1_1(self):
        request = webob.Request.blank('/v1.1/fake/images')
        response = request.get_response(fakes.wsgi_app())

        response_dict = json.loads(response.body)
        response_list = response_dict["images"]

        fixtures = copy.copy(self.fixtures)

        for image in fixtures:
            if not self._applicable_fixture(image, "fake"):
                fixtures.remove(image)
                continue

            href = "http://localhost/v1.1/fake/images/%s" % image["id"]
            bookmark = "http://localhost/fake/images/%s" % image["id"]
            test_image = {
                "id": image["id"],
                "name": image["name"],
                "links": [
                    {
                        "rel": "self",
                        "href": href,
                    },
                    {
                        "rel": "bookmark",
                        "href": bookmark,
                    },
                ],
            }
            self.assertTrue(test_image in response_list)

        self.assertEqual(len(response_list), len(fixtures))

    def test_get_image_details(self):
        request = webob.Request.blank('/v1.0/images/detail')
        response = request.get_response(fakes.wsgi_app())

        response_dict = json.loads(response.body)
        response_list = response_dict["images"]

        expected = [{
            'id': 123,
            'name': 'public image',
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'ACTIVE',
            'progress': 100,
        },
        {
            'id': 124,
            'name': 'queued snapshot',
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'QUEUED',
            'progress': 0,
        },
        {
            'id': 125,
            'name': 'saving snapshot',
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'SAVING',
            'progress': 0,
        },
        {
            'id': 126,
            'name': 'active snapshot',
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'ACTIVE',
            'progress': 100,
        },
        {
            'id': 127,
            'name': 'killed snapshot',
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'FAILED',
            'progress': 0,
        },
        {
            'id': 129,
            'name': None,
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'ACTIVE',
            'progress': 100,
        }]

        self.assertDictListMatch(expected, response_list)

    def test_get_image_details_v1_1(self):
        request = webob.Request.blank('/v1.1/fake/images/detail')
        response = request.get_response(fakes.wsgi_app())

        response_dict = json.loads(response.body)
        response_list = response_dict["images"]
        server_href = "http://localhost/v1.1/servers/42"
        server_bookmark = "http://localhost/servers/42"

        expected = [{
            'id': 123,
            'name': 'public image',
            'metadata': {},
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'ACTIVE',
            'progress': 100,
            "links": [{
                "rel": "self",
                "href": "http://localhost/v1.1/fake/images/123",
            },
            {
                "rel": "bookmark",
                "href": "http://localhost/fake/images/123",
            }],
        },
        {
            'id': 124,
            'name': 'queued snapshot',
            'metadata': {
                u'instance_ref': u'http://localhost/v1.1/servers/42',
                u'user_id': u'fake',
            },
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'QUEUED',
            'progress': 0,
            'server': {
                'id': 42,
                "links": [{
                    "rel": "self",
                    "href": server_href,
                },
                {
                    "rel": "bookmark",
                    "href": server_bookmark,
                }],
            },
            "links": [{
                "rel": "self",
                "href": "http://localhost/v1.1/fake/images/124",
            },
            {
                "rel": "bookmark",
                "href": "http://localhost/fake/images/124",
            }],
        },
        {
            'id': 125,
            'name': 'saving snapshot',
            'metadata': {
                u'instance_ref': u'http://localhost/v1.1/servers/42',
                u'user_id': u'fake',
            },
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'SAVING',
            'progress': 0,
            'server': {
                'id': 42,
                "links": [{
                    "rel": "self",
                    "href": server_href,
                },
                {
                    "rel": "bookmark",
                    "href": server_bookmark,
                }],
            },
            "links": [{
                "rel": "self",
                "href": "http://localhost/v1.1/fake/images/125",
            },
            {
                "rel": "bookmark",
                "href": "http://localhost/fake/images/125",
            }],
        },
        {
            'id': 126,
            'name': 'active snapshot',
            'metadata': {
                u'instance_ref': u'http://localhost/v1.1/servers/42',
                u'user_id': u'fake',
            },
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'ACTIVE',
            'progress': 100,
            'server': {
                'id': 42,
                "links": [{
                    "rel": "self",
                    "href": server_href,
                },
                {
                    "rel": "bookmark",
                    "href": server_bookmark,
                }],
            },
            "links": [{
                "rel": "self",
                "href": "http://localhost/v1.1/fake/images/126",
            },
            {
                "rel": "bookmark",
                "href": "http://localhost/fake/images/126",
            }],
        },
        {
            'id': 127,
            'name': 'killed snapshot',
            'metadata': {
                u'instance_ref': u'http://localhost/v1.1/servers/42',
                u'user_id': u'fake',
            },
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'FAILED',
            'progress': 0,
            'server': {
                'id': 42,
                "links": [{
                    "rel": "self",
                    "href": server_href,
                },
                {
                    "rel": "bookmark",
                    "href": server_bookmark,
                }],
            },
            "links": [{
                "rel": "self",
                "href": "http://localhost/v1.1/fake/images/127",
            },
            {
                "rel": "bookmark",
                "href": "http://localhost/fake/images/127",
            }],
        },
        {
            'id': 129,
            'name': None,
            'metadata': {},
            'updated': self.NOW_API_FORMAT,
            'created': self.NOW_API_FORMAT,
            'status': 'ACTIVE',
            'progress': 100,
            "links": [{
                "rel": "self",
                "href": "http://localhost/v1.1/fake/images/129",
            },
            {
                "rel": "bookmark",
                "href": "http://localhost/fake/images/129",
            }],
        },
        ]

        self.assertDictListMatch(expected, response_list)

    def test_image_filter_with_name(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'name': 'testname'}
        image_service.index(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/images?name=testname')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_filter_with_status(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'status': 'ACTIVE'}
        image_service.index(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/images?status=ACTIVE')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_filter_with_property(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'property-test': '3'}
        image_service.index(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/images?property-test=3')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_filter_server(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        # 'server' should be converted to 'property-instance_ref'
        filters = {'property-instance_ref': 'http://localhost:8774/servers/12'}
        image_service.index(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/images?server='
                                      'http://localhost:8774/servers/12')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_filter_changes_since(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'changes-since': '2011-01-24T17:08Z'}
        image_service.index(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/images?changes-since='
                                      '2011-01-24T17:08Z')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_filter_with_type(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'property-image_type': 'BASE'}
        image_service.index(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/images?type=BASE')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_filter_not_supported(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'status': 'ACTIVE'}
        image_service.detail(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/images?status=ACTIVE&'
                                      'UNSUPPORTEDFILTER=testname')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.detail(request)
        self.mox.VerifyAll()

    def test_image_no_filters(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {}
        image_service.index(
            context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank(
            '/v1.1/images')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_detail_filter_with_name(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'name': 'testname'}
        image_service.detail(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/fake/images/detail?name=testname')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.detail(request)
        self.mox.VerifyAll()

    def test_image_detail_filter_with_status(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'status': 'ACTIVE'}
        image_service.detail(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/fake/images/detail?status=ACTIVE')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.detail(request)
        self.mox.VerifyAll()

    def test_image_detail_filter_with_property(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'property-test': '3'}
        image_service.detail(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank(
            '/v1.1/fake/images/detail?property-test=3')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.detail(request)
        self.mox.VerifyAll()

    def test_image_detail_filter_server(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        # 'server' should be converted to 'property-instance_ref'
        filters = {'property-instance_ref': 'http://localhost:8774/servers/12'}
        image_service.index(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/fake/images/detail?server='
                                      'http://localhost:8774/servers/12')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_detail_filter_changes_since(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'changes-since': '2011-01-24T17:08Z'}
        image_service.index(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/fake/images/detail?changes-since='
                                      '2011-01-24T17:08Z')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_detail_filter_with_type(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'property-image_type': 'BASE'}
        image_service.index(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/fake/images/detail?type=BASE')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.index(request)
        self.mox.VerifyAll()

    def test_image_detail_filter_not_supported(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {'status': 'ACTIVE'}
        image_service.detail(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/fake/images/detail?status=ACTIVE&'
                                      'UNSUPPORTEDFILTER=testname')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.detail(request)
        self.mox.VerifyAll()

    def test_image_detail_no_filters(self):
        image_service = self.mox.CreateMockAnything()
        context = self._get_fake_context()
        filters = {}
        image_service.detail(context, filters=filters).AndReturn([])
        self.mox.ReplayAll()
        request = webob.Request.blank('/v1.1/fake/images/detail')
        request.environ['nova.context'] = context
        controller = images.ControllerV11(image_service=image_service)
        controller.detail(request)
        self.mox.VerifyAll()

    def test_get_image_found(self):
        req = webob.Request.blank('/v1.0/images/123')
        res = req.get_response(fakes.wsgi_app())
        image_meta = json.loads(res.body)['image']
        expected = {'id': 123, 'name': 'public image',
                    'updated': self.NOW_API_FORMAT,
                    'created': self.NOW_API_FORMAT, 'status': 'ACTIVE',
                    'progress': 100}
        self.assertDictMatch(image_meta, expected)

    def test_get_image_non_existent(self):
        req = webob.Request.blank('/v1.0/images/4242')
        res = req.get_response(fakes.wsgi_app())
        self.assertEqual(res.status_int, 404)

    def test_get_image_not_owned(self):
        """We should return a 404 if we request an image that doesn't belong
        to us
        """
        req = webob.Request.blank('/v1.0/images/128')
        res = req.get_response(fakes.wsgi_app())
        self.assertEqual(res.status_int, 404)

    def test_create_image(self):
        body = dict(image=dict(serverId='123', name='Snapshot 1'))
        req = webob.Request.blank('/v1.0/images')
        req.method = 'POST'
        req.body = json.dumps(body)
        req.headers["content-type"] = "application/json"
        response = req.get_response(fakes.wsgi_app())
        self.assertEqual(200, response.status_int)
        image_meta = json.loads(response.body)['image']
        self.assertEqual(123, image_meta['serverId'])
        self.assertEqual('Snapshot 1', image_meta['name'])

    def test_create_snapshot_no_name(self):
        """Name is required for snapshots"""
        body = dict(image=dict(serverId='123'))
        req = webob.Request.blank('/v1.0/images')
        req.method = 'POST'
        req.body = json.dumps(body)
        req.headers["content-type"] = "application/json"
        response = req.get_response(fakes.wsgi_app())
        self.assertEqual(400, response.status_int)

    def test_create_image_no_server_id(self):

        body = dict(image=dict(name='Snapshot 1'))
        req = webob.Request.blank('/v1.0/images')
        req.method = 'POST'
        req.body = json.dumps(body)
        req.headers["content-type"] = "application/json"
        response = req.get_response(fakes.wsgi_app())
        self.assertEqual(400, response.status_int)

    def test_create_image_snapshots_disabled(self):
        self.flags(allow_instance_snapshots=False)
        body = dict(image=dict(serverId='123', name='Snapshot 1'))
        req = webob.Request.blank('/v1.0/images')
        req.method = 'POST'
        req.body = json.dumps(body)
        req.headers["content-type"] = "application/json"
        response = req.get_response(fakes.wsgi_app())
        self.assertEqual(400, response.status_int)

    @classmethod
    def _make_image_fixtures(cls):
        image_id = 123
        base_attrs = {'created_at': cls.NOW_GLANCE_FORMAT,
                      'updated_at': cls.NOW_GLANCE_FORMAT,
                      'deleted_at': None,
                      'deleted': False}

        fixtures = []

        def add_fixture(**kwargs):
            kwargs.update(base_attrs)
            fixtures.append(kwargs)

        # Public image
        add_fixture(id=image_id, name='public image', is_public=True,
                    status='active', properties={})
        image_id += 1

        # Snapshot for User 1
        server_ref = 'http://localhost/v1.1/servers/42'
        snapshot_properties = {'instance_ref': server_ref, 'user_id': 'fake'}
        for status in ('queued', 'saving', 'active', 'killed'):
            add_fixture(id=image_id, name='%s snapshot' % status,
                        is_public=False, status=status,
                        properties=snapshot_properties)
            image_id += 1

        # Snapshot for User 2
        other_snapshot_properties = {'instance_id': '43', 'user_id': 'other'}
        add_fixture(id=image_id, name='someone elses snapshot',
                    is_public=False, status='active',
                    properties=other_snapshot_properties)

        image_id += 1

        # Image without a name
        add_fixture(id=image_id, is_public=True, status='active',
                    properties={})
        image_id += 1

        return fixtures


class ImageXMLSerializationTest(test.TestCase):

    TIMESTAMP = "2010-10-11T10:30:22Z"
    SERVER_HREF = 'http://localhost/v1.1/servers/123'
    SERVER_BOOKMARK = 'http://localhost/servers/123'
    IMAGE_HREF = 'http://localhost/v1.1/fake/images/%s'
    IMAGE_BOOKMARK = 'http://localhost/fake/images/%s'

    def test_show(self):
        serializer = images.ImageXMLSerializer()

        fixture = {
            'image': {
                'id': 1,
                'name': 'Image1',
                'created': self.TIMESTAMP,
                'updated': self.TIMESTAMP,
                'status': 'ACTIVE',
                'progress': 80,
                'server': {
                    'id': 1,
                    'links': [
                        {
                            'href': self.SERVER_HREF,
                            'rel': 'self',
                        },
                        {
                            'href': self.SERVER_BOOKMARK,
                            'rel': 'bookmark',
                        },
                    ],
                },
                'metadata': {
                    'key1': 'value1',
                },
                'links': [
                    {
                        'href': self.IMAGE_HREF % 1,
                        'rel': 'self',
                    },
                    {
                        'href': self.IMAGE_BOOKMARK % 1,
                        'rel': 'bookmark',
                    },
                ],
            },
        }

        output = serializer.serialize(fixture, 'show')
        actual = minidom.parseString(output.replace("  ", ""))

        expected_server_href = self.SERVER_HREF
        expected_server_bookmark = self.SERVER_BOOKMARK
        expected_href = self.IMAGE_HREF % 1
        expected_bookmark = self.IMAGE_BOOKMARK % 1
        expected_now = self.TIMESTAMP
        expected = minidom.parseString("""
        <image id="1"
                xmlns="http://docs.openstack.org/compute/api/v1.1"
                xmlns:atom="http://www.w3.org/2005/Atom"
                name="Image1"
                updated="%(expected_now)s"
                created="%(expected_now)s"
                status="ACTIVE"
                progress="80">
            <server id="1">
                <atom:link rel="self" href="%(expected_server_href)s"/>
                <atom:link rel="bookmark" href="%(expected_server_bookmark)s"/>
            </server>
            <metadata>
                <meta key="key1">
                    value1
                </meta>
            </metadata>
            <atom:link href="%(expected_href)s" rel="self"/>
            <atom:link href="%(expected_bookmark)s" rel="bookmark"/>
        </image>
        """.replace("  ", "") % (locals()))

        self.assertEqual(expected.toxml(), actual.toxml())

    def test_show_zero_metadata(self):
        serializer = images.ImageXMLSerializer()

        fixture = {
            'image': {
                'id': 1,
                'name': 'Image1',
                'created': self.TIMESTAMP,
                'updated': self.TIMESTAMP,
                'status': 'ACTIVE',
                'server': {
                    'id': 1,
                    'links': [
                        {
                            'href': self.SERVER_HREF,
                            'rel': 'self',
                        },
                        {
                            'href': self.SERVER_BOOKMARK,
                            'rel': 'bookmark',
                        },
                    ],
                },
                'metadata': {},
                'links': [
                    {
                        'href': self.IMAGE_HREF % 1,
                        'rel': 'self',
                    },
                    {
                        'href': self.IMAGE_BOOKMARK % 1,
                        'rel': 'bookmark',
                    },
                ],
            },
        }

        output = serializer.serialize(fixture, 'show')
        actual = minidom.parseString(output.replace("  ", ""))

        expected_server_href = self.SERVER_HREF
        expected_server_bookmark = self.SERVER_BOOKMARK
        expected_href = self.IMAGE_HREF % 1
        expected_bookmark = self.IMAGE_BOOKMARK % 1
        expected_now = self.TIMESTAMP
        expected = minidom.parseString("""
        <image id="1"
                xmlns="http://docs.openstack.org/compute/api/v1.1"
                xmlns:atom="http://www.w3.org/2005/Atom"
                name="Image1"
                updated="%(expected_now)s"
                created="%(expected_now)s"
                status="ACTIVE">
            <server id="1">
                <atom:link rel="self" href="%(expected_server_href)s"/>
                <atom:link rel="bookmark" href="%(expected_server_bookmark)s"/>
            </server>
            <atom:link href="%(expected_href)s" rel="self"/>
            <atom:link href="%(expected_bookmark)s" rel="bookmark"/>
        </image>
        """.replace("  ", "") % (locals()))

        self.assertEqual(expected.toxml(), actual.toxml())

    def test_show_image_no_metadata_key(self):
        serializer = images.ImageXMLSerializer()

        fixture = {
            'image': {
                'id': 1,
                'name': 'Image1',
                'created': self.TIMESTAMP,
                'updated': self.TIMESTAMP,
                'status': 'ACTIVE',
                'server': {
                    'id': 1,
                    'links': [
                        {
                            'href': self.SERVER_HREF,
                            'rel': 'self',
                        },
                        {
                            'href': self.SERVER_BOOKMARK,
                            'rel': 'bookmark',
                        },
                    ],
                },
                'links': [
                    {
                        'href': self.IMAGE_HREF % 1,
                        'rel': 'self',
                    },
                    {
                        'href': self.IMAGE_BOOKMARK % 1,
                        'rel': 'bookmark',
                    },
                ],
            },
        }

        output = serializer.serialize(fixture, 'show')
        actual = minidom.parseString(output.replace("  ", ""))

        expected_server_href = self.SERVER_HREF
        expected_server_bookmark = self.SERVER_BOOKMARK
        expected_href = self.IMAGE_HREF % 1
        expected_bookmark = self.IMAGE_BOOKMARK % 1
        expected_now = self.TIMESTAMP
        expected = minidom.parseString("""
        <image id="1"
                xmlns="http://docs.openstack.org/compute/api/v1.1"
                xmlns:atom="http://www.w3.org/2005/Atom"
                name="Image1"
                updated="%(expected_now)s"
                created="%(expected_now)s"
                status="ACTIVE">
            <server id="1">
                <atom:link rel="self" href="%(expected_server_href)s"/>
                <atom:link rel="bookmark" href="%(expected_server_bookmark)s"/>
            </server>
            <atom:link href="%(expected_href)s" rel="self"/>
            <atom:link href="%(expected_bookmark)s" rel="bookmark"/>
        </image>
        """.replace("  ", "") % (locals()))

        self.assertEqual(expected.toxml(), actual.toxml())

    def test_show_no_server(self):
        serializer = images.ImageXMLSerializer()

        fixture = {
            'image': {
                'id': 1,
                'name': 'Image1',
                'created': self.TIMESTAMP,
                'updated': self.TIMESTAMP,
                'status': 'ACTIVE',
                'metadata': {
                    'key1': 'value1',
                },
                'links': [
                    {
                        'href': self.IMAGE_HREF % 1,
                        'rel': 'self',
                    },
                    {
                        'href': self.IMAGE_BOOKMARK % 1,
                        'rel': 'bookmark',
                    },
                ],
            },
        }

        output = serializer.serialize(fixture, 'show')
        actual = minidom.parseString(output.replace("  ", ""))

        expected_href = self.IMAGE_HREF % 1
        expected_bookmark = self.IMAGE_BOOKMARK % 1
        expected_now = self.TIMESTAMP
        expected = minidom.parseString("""
        <image id="1"
                xmlns="http://docs.openstack.org/compute/api/v1.1"
                xmlns:atom="http://www.w3.org/2005/Atom"
                name="Image1"
                updated="%(expected_now)s"
                created="%(expected_now)s"
                status="ACTIVE">
            <metadata>
                <meta key="key1">
                    value1
                </meta>
            </metadata>
            <atom:link href="%(expected_href)s" rel="self"/>
            <atom:link href="%(expected_bookmark)s" rel="bookmark"/>
        </image>
        """.replace("  ", "") % (locals()))

        self.assertEqual(expected.toxml(), actual.toxml())

    def test_index(self):
        serializer = images.ImageXMLSerializer()

        fixture = {
            'images': [
                {
                    'id': 1,
                    'name': 'Image1',
                    'links': [
                        {
                            'href': self.IMAGE_HREF % 1,
                            'rel': 'self',
                        },
                    ],
                },
                {
                    'id': 2,
                    'name': 'Image2',
                    'links': [
                        {
                            'href': self.IMAGE_HREF % 2,
                            'rel': 'self',
                        },
                    ],
                },
            ]
        }

        output = serializer.serialize(fixture, 'index')
        actual = minidom.parseString(output.replace("  ", ""))

        expected_server_href = self.SERVER_HREF
        expected_server_bookmark = self.SERVER_BOOKMARK
        expected_href = self.IMAGE_HREF % 1
        expected_bookmark = self.IMAGE_BOOKMARK % 1
        expected_href_two = self.IMAGE_HREF % 2
        expected_bookmark_two = self.IMAGE_BOOKMARK % 2
        expected_now = self.TIMESTAMP
        expected = minidom.parseString("""
        <images
                xmlns="http://docs.openstack.org/compute/api/v1.1"
                xmlns:atom="http://www.w3.org/2005/Atom">
        <image id="1" name="Image1">
            <atom:link href="%(expected_href)s" rel="self"/>
        </image>
        <image id="2" name="Image2">
            <atom:link href="%(expected_href_two)s" rel="self"/>
        </image>
        </images>
        """.replace("  ", "") % (locals()))

        self.assertEqual(expected.toxml(), actual.toxml())

    def test_index_zero_images(self):
        serializer = images.ImageXMLSerializer()

        fixtures = {
            'images': [],
        }

        output = serializer.serialize(fixtures, 'index')
        actual = minidom.parseString(output.replace("  ", ""))

        expected = minidom.parseString("""
        <images
                xmlns="http://docs.openstack.org/compute/api/v1.1"
                xmlns:atom="http://www.w3.org/2005/Atom" />
        """.replace("  ", "") % (locals()))

        self.assertEqual(expected.toxml(), actual.toxml())

    def test_detail(self):
        serializer = images.ImageXMLSerializer()

        fixture = {
            'images': [
                {
                    'id': 1,
                    'name': 'Image1',
                    'created': self.TIMESTAMP,
                    'updated': self.TIMESTAMP,
                    'status': 'ACTIVE',
                    'server': {
                        'id': 1,
                        'links': [
                            {
                                'href': self.SERVER_HREF,
                                'rel': 'self',
                            },
                            {
                                'href': self.SERVER_BOOKMARK,
                                'rel': 'bookmark',
                            },
                        ],
                    },
                    'links': [
                        {
                            'href': self.IMAGE_HREF % 1,
                            'rel': 'self',
                        },
                        {
                            'href': self.IMAGE_BOOKMARK % 1,
                            'rel': 'bookmark',
                        },
                    ],
                },
                {
                    'id': 2,
                    'name': 'Image2',
                    'created': self.TIMESTAMP,
                    'updated': self.TIMESTAMP,
                    'status': 'SAVING',
                    'progress': 80,
                    'metadata': {
                        'key1': 'value1',
                    },
                    'links': [
                        {
                            'href': self.IMAGE_HREF % 2,
                            'rel': 'self',
                        },
                        {
                            'href': self.IMAGE_BOOKMARK % 2,
                            'rel': 'bookmark',
                        },
                    ],
                },
            ]
        }

        output = serializer.serialize(fixture, 'detail')
        actual = minidom.parseString(output.replace("  ", ""))

        expected_server_href = self.SERVER_HREF
        expected_server_bookmark = self.SERVER_BOOKMARK
        expected_href = self.IMAGE_HREF % 1
        expected_bookmark = self.IMAGE_BOOKMARK % 1
        expected_href_two = self.IMAGE_HREF % 2
        expected_bookmark_two = self.IMAGE_BOOKMARK % 2
        expected_now = self.TIMESTAMP
        expected = minidom.parseString("""
        <images
                xmlns="http://docs.openstack.org/compute/api/v1.1"
                xmlns:atom="http://www.w3.org/2005/Atom">
        <image id="1"
                name="Image1"
                updated="%(expected_now)s"
                created="%(expected_now)s"
                status="ACTIVE">
            <server id="1">
                <atom:link rel="self" href="%(expected_server_href)s"/>
                <atom:link rel="bookmark" href="%(expected_server_bookmark)s"/>
            </server>
            <atom:link href="%(expected_href)s" rel="self"/>
            <atom:link href="%(expected_bookmark)s" rel="bookmark"/>
        </image>
        <image id="2"
                name="Image2"
                updated="%(expected_now)s"
                created="%(expected_now)s"
                status="SAVING"
                progress="80">
            <metadata>
                <meta key="key1">
                    value1
                </meta>
            </metadata>
            <atom:link href="%(expected_href_two)s" rel="self"/>
            <atom:link href="%(expected_bookmark_two)s" rel="bookmark"/>
        </image>
        </images>
        """.replace("  ", "") % (locals()))

        self.assertEqual(expected.toxml(), actual.toxml())
