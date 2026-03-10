from pyspark.sql import SparkSession

spark = SparkSession.builder \
    .appName("Storage Connectivity Test") \
    .master("spark://ubuntu:7077") \
    .config("spark.driver.memory", "1g") \
    .config("spark.driver.cores", "3") \
    .config("spark.executor.memory", "1g") \
    .config("spark.executor.cores", "1") \
    .getOrCreate()

try:
    # Test reading from GCS
    print("Reading from GCS bucket.....")
    gcs_df = spark.read.option('inferSchema','true').option('header','true').csv("gs://homelab-data-lake/df_open_2011.csv")
    print(f"Successfully read {gcs_df.count()} records from GCS")
    gcs_df.show(5)

    # Test reading from S3
    print("Reading from s3 bucket.....")
    s3_df = spark.read.option('inferSchema','true').option('header','true').csv("s3a://homelab-data-lake/dev/games/df_games_and_open.csv")
    print(f"Successfully read {s3_df.count()} records from GCS")
    s3_df.show(5)

except Exception as e:
    print(f"Error accessing GCS: {str(e)}")
spark.stop()