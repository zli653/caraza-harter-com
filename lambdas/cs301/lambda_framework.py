# Copyright 2018 Tyler Caraza-Harter
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json, urllib, boto3, botocore, base64, time, traceback, random, string
from collections import defaultdict as ddict

def file_lines(path):
    users = []
    with open(path) as f:
        for l in f:
            l = l.strip()
            if l:
                users.append(l)
    return users

ROUTES = {}
EXTRA_AUTH = ddict(list)
BUCKET = 'caraza-harter-cs301'
FIREHOSE = 'cs301-trace'

# ACLs
ADMIN_EMAIL = 'tylerharter@gmail.com'
INSTRUCTOR_EMAILS = file_lines("instructors.txt")
GRADER_EMAILS = file_lines("tas.txt")

class S3:
    def __init__(self, s3_client, key_prefix):
        self.s3_client = s3_client
        self.key_prefix = key_prefix

    # S3 client wrappers that prepend the dir prefix
    def list_objects_v2(self, **kwargs):
        kwargs["Prefix"] = self.key_prefix + "/" + kwargs["Prefix"]
        return self.s3_client.list_objects_v2(**kwargs)

    def head_object(self, **kwargs):
        kwargs["Key"] = self.key_prefix + "/" + kwargs["Key"]
        return self.s3_client.head_object(**kwargs)
    
    def get_object(self, **kwargs):
        kwargs["Key"] = self.key_prefix + "/" + kwargs["Key"]
        return self.s3_client.get_object(**kwargs)

    def put_object(self, **kwargs):
        kwargs["Key"] = self.key_prefix + "/" + kwargs["Key"]
        return self.s3_client.put_object(**kwargs)

    def delete_object(self, **kwargs):
        kwargs["Key"] = self.key_prefix + "/" + kwargs["Key"]
        return self.s3_client.delete_object(**kwargs)

    # convenience functions beyond what the boto client provides
    def s3_all_keys(self, Prefix):
        ls = self.list_objects_v2(Bucket=BUCKET, Prefix=Prefix, MaxKeys=10000)
        keys = []
        while True:
            contents = [obj['Key'] for obj in ls.get('Contents',[])]
            keys.extend(contents)
            if not 'NextContinuationToken' in ls:
                break
            ls = s3().list_objects_v2(Bucket='caraza-harter-cs301', Prefix=Prefix,
                                      ContinuationToken=ls['NextContinuationToken'],
                                      MaxKeys=10000)
        return keys

    def path_exists(self, path):
        try:
            self.head_object(Bucket=BUCKET, Key=path)
            return True
        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise e

# generic S3 client
boto_s3_client = None

# our wrapper that does everything within a subdir (each course is a different dir)
s3_client = None

def init_s3(prefix):
    global boto_s3_client, s3_client
    if boto_s3_client == None:
        boto_s3_client = boto3.client('s3')
    s3_client = S3(boto_s3_client, prefix)

def s3():
    return s3_client

firehose_cache = None # client
def firehose():
    # cache firehose client
    global firehose_cache
    if firehose_cache == None:
        firehose_cache = boto3.client('firehose')
    return firehose_cache

# decorators
def route(fn):
    ROUTES[fn.__name__] = fn
    return fn

# decorator: user must authenticate to the admin user
def admin(fn):
    EXTRA_AUTH[fn.__name__].append(admin_check)
    return fn

def instructor(fn):
    EXTRA_AUTH[fn.__name__].append(instructor_check)
    return fn

# decorator: user must authenticate and have a valid email
def user(fn):
    EXTRA_AUTH[fn.__name__].append(user_check)
    return fn

# decorator: user must authenticate and be a grader
def grader(fn):
    EXTRA_AUTH[fn.__name__].append(grader_check)
    return fn

def user_check(user):
    if user == None:
        raise Exception('could not authenticate user with google')
    if not user['email_verified']:
        raise Exception('google email not verified')

def admin_check(user):
    user_check(user)
    if user['email'] != ADMIN_EMAIL:
        raise Exception('admin permissions required')

def instructor_check(user):
    user_check(user)
    if not user['email'] in INSTRUCTOR_EMAILS:
        raise Exception('instructor permissions required')

def grader_check(user):
    user_check(user)
    if not user['email'] in GRADER_EMAILS:
        raise Exception('grader permissions required')

def is_grader(user):
    return user['email'] in GRADER_EMAILS

# TODO: cache this
def get_user(event):
    token = event['GoogleToken']
    req = urllib.request.Request('https://www.googleapis.com/oauth2/v3/tokeninfo?id_token='+token)
    response = urllib.request.urlopen(req)
    status = json.loads(response.read())
    return status

def error(message):
    return {
        "isBase64Encoded": False,
        "statusCode": 500,
        "headers": {},
        "body": message
    }

def save_user_info(user):
    path = 'users/google/%s.json' % user['sub']
    try:
        boto3.resource('s3').Object(BUCKET, path).load()
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "404":
            # does not exist yet
            s3().put_object(Bucket=BUCKET,
                          Key=path,
                          Body=bytes(json.dumps(user, indent=2), 'utf-8'),
                          ContentType='text/json',
            )
        else:
            raise e
