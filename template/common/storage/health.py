"""S3 storage health check."""

from botocore.exceptions import ClientError

from common.storage.s3_service import S3Service


def check_s3_health(s3_service: S3Service) -> tuple[bool, str]:
    """Check S3 storage connectivity.

    Args:
        s3_service: S3Service instance to check.

    Returns:
        Tuple of (healthy, message).
    """
    try:
        # Try to check if the bucket exists/is accessible
        bucket_name = s3_service.settings.S3_BUCKET_NAME
        s3_service.s3_client.head_bucket(Bucket=bucket_name)
        return True, "S3 storage is accessible"
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        bucket_name = s3_service.settings.S3_BUCKET_NAME
        if error_code == "404":
            return False, f"S3 bucket '{bucket_name}' not found"
        elif error_code == "403":
            return False, f"S3 bucket '{bucket_name}' access denied"
        return False, f"S3 error: {error_code}"
    except Exception as e:
        return False, f"S3 connection failed: {e}"
