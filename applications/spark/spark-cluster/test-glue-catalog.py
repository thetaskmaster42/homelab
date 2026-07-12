## WIP - Testing Glue Catalog integration with Spark

from pyspark.sql import SparkSession
spark = (
    SparkSession.builder.appName("LocalGlueCatalogSpark")
    .config("spark.driver.memory", "4g")
    .config("spark.executor.memory", "6g")
    .config("spark.executor.cores", "2")
    .config("spar.driver.maxResultSize", "1g")
    .config("spark.master", "spark://ubuntu:7077")
    .config("spark.driver.cores", "6")
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    .config("fs.s3a.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
    .config("fs.s3a.region", "us-west-2")
    .enableHiveSupport()
    .getOrCreate()
)

database = "test_db"
destination_table = "games_parquet_2"
catalog = "spark_catalog"
try:
    # List databases in Glue Catalog
    databases = spark.sql("SHOW DATABASES")
    print("Databases in Glue Catalog:")
    databases.show()
    
    create_database_query = f"CREATE DATABASE IF NOT EXISTS {catalog}.{database}"
    spark.sql(create_database_query)

    df = spark.read.option("header", "true").option("inferSchema", "true").csv("s3a://homelab-data-lake/dev/games/df_games_and_open.csv")
    print(f"Successfully read {df.count()} records from S3")
    df.show(5)

    df.write.format("parquet").saveAsTable(f"{catalog}.{database}.{destination_table}")
    games_parquet_df = spark.sql(f"SELECT * FROM {catalog}.{database}.{destination_table} LIMIT 5")
    print("Sample data from Parquet table in Glue Catalog:")
    games_parquet_df.show() 
except Exception as e:
    print(f"Error accessing Glue Catalog: {str(e)}") 