import boto3

session = boto3.session.Session()
client = session.client("s3", region_name="ap-southeast-1")  # or whatever region you discovered
print(client.meta.endpoint_url)