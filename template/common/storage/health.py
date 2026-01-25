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
        s3_service.client.head_bucket(Bucket=s3_service.bucket)
        return True, "S3 storage is accessible"
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "404":
            return False, f"S3 bucket '{s3_service.bucket}' not found"
        elif error_code == "403":
            return False, f"S3 bucket '{s3_service.bucket}' access denied"
        return False, f"S3 error: {error_code}"
    except Exception as e:
        return False, f"S3 connection failed: {e}"
