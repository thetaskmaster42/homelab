
# default working in local
```bash 
pyspark \
--master spark://ubuntu:7077 \
--deploy-mode client \
--conf spark.app.name=HomelabSparkApp \
--conf spark.driver.memory=1g \
--conf spark.driver.cores=3 \
--conf spark.executor.memory=1g \
--conf spark.executor.cores=1 \
--conf spark.sql.catalog.metastore.type=hive 
```

## Reading for AWS S3 
```bash
>>> s3_df = spark.read.option('inferSchema','true').option('header','true').csv("s3a://homelab-data-lake/dev/games/df_games_and_open.csv")
>>> s3_df.show(5)
+------------+-----------------+-----------+--------+------+--------+---+-------+------+----------+------+----------+------------------+-------+----------------+-----------+-------------------+-------------------+-------------+--------+------+--------+----+-----------+------------+--------------+--------+---------+-----------------+----------------+
|competitorId|   competitorName|  firstName|lastName|gender|genderId|age|ageNull|height|heightNull|weight|weightNull|               bmi|bmiNull|   affiliateName|affiliateId|countryOfOriginName|countryOfOriginCode|   regionName|regionId|status|statusId|year|overallRank|overallScore|openCompetitor|openRank|openScore|gamesCompetitions|openCompetitions|
+------------+-----------------+-----------+--------+------+--------+---+-------+------+----------+------+----------+------------------+-------+----------------+-----------+-------------------+-------------------+-------------+--------+------+--------+----+-----------+------------+--------------+--------+---------+-----------------+----------------+
|        1616|      Russ Greene|       Russ|  Greene|     M|       1| 20|      0| 178.0|         0|  83.0|         0| 26.19618735008206|      0|            NULL|          0|               NULL|                  0|         NULL|       0|   ACT|       1|2007|         11|         232|             0|    1000|     5000|                1|               0|
|        1616|      Russ Greene|       Russ|  Greene|     M|       1| 21|      0| 178.0|         0|  83.0|         0| 26.19618735008206|      0|            NULL|          0|               NULL|                  0|         NULL|       0|   ACT|       1|2008|         53|          21|             0|    1000|     5000|                2|               0|
|        1685|Christopher Woods|Christopher|   Woods|     M|       1| 29|      0| 163.0|         0|  82.0|         0|30.863035868869737|      0|            NULL|          0|               NULL|                  0|         NULL|       0|   ACT|       1|2008|         32|          19|             0|    1000|     5000|                1|               0|
|        1690|     Travis Mayer|     Travis|   Mayer|     M|       1| 23|      0| 181.0|         0|  93.0|         0|28.387411861664784|      0|CrossFit Passion|       7104|      United States|                  1|North America|       1|   ACT|       1|2014|         29|         483|             1|      17|      566|                1|               1|
|        1690|     Travis Mayer|     Travis|   Mayer|     M|       1| 25|      0| 181.0|         0|  93.0|         0|28.387411861664784|      0|CrossFit Passion|       7104|      United States|                  1|North America|       1|   ACT|       1|2016|         10|         702|             1|       3|       86|                2|               2|
+------------+-----------------+-----------+--------+------+--------+---+-------+------+----------+------+----------+------------------+-------+----------------+-----------+-------------------+-------------------+-------------+--------+------+--------+----+-----------+------------+--------------+--------+---------+-----------------+----------------+
only showing top 5 rows
```

## Reading from GCS
```
>>> gcs_df = spark.read.option('inferSchema','true').option('header','true').csv("gs://homelab-data-lake/df_open_2011.csv")
>>> gcs_df.show(5)
+------------+---------------+---------+--------+------+------+-------------------+-------------------+--------+-------------------+-----------+-------------------+---+------+------+-----------+------------+--------+----+
|competitorId| competitorName|firstName|lastName|status|gender|countryOfOriginCode|countryOfOriginName|regionId|         regionName|affiliateId|      affiliateName|age|height|weight|overallRank|overallScore|genderId|year|
+------------+---------------+---------+--------+------+------+-------------------+-------------------+--------+-------------------+-----------+-------------------+---+------+------+-----------+------------+--------+----+
|       47661|     Dan Bailey|      Dan|  Bailey|   ACT|     M|               NULL|               NULL|       6|       Central East|          0|    CrossFit Legacy| 27|  NULL|  NULL|          1|          43|       1|2011|
|      124483| Joshua Bridges|   Joshua| Bridges|   ACT|     M|               NULL|               NULL|      16|Southern California|          0|  CrossFit Invictus| 28|  NULL|  NULL|          2|          44|       1|2011|
|       11435|   Rich Froning|     Rich| Froning|   ACT|     M|               NULL|               NULL|       6|       Central East|          0|     CrossFit Faith| 23|  NULL|  NULL|          3|          61|       1|2011|
|      151906|     Mikko Salo|    Mikko|    Salo|   ACT|     M|               NULL|               NULL|       7|             Europe|          0|      CrossFit Pori| 31|  NULL|  NULL|          4|          75|       1|2011|
|       10169|Austin Malleolo|   Austin|Malleolo|   ACT|     M|               NULL|               NULL|      11|         North East|          0|Reebok CrossFit One| 24|  NULL|  NULL|          5|         112|       1|2011|
+------------+---------------+---------+--------+------+------+-------------------+-------------------+--------+-------------------+-----------+-------------------+---+------+------+-----------+------------+--------+----+
only showing top 5 rows
```


## spark Session

```python
# create a file test-storage-connectivity.py
from pyspark.sql import SparkSession

spark = SparkSession.builder \
    .appName("Storage Connectivity Test") \
    .getOrCreate()

try:
    # Test reading from GCS
    print("Reading from GCS bucket....."
    gcs_df = spark.read.option('inferSchema','true').option('header','true').csv("gs://homelab-data-lake/df_open_2011.csv")
    print(f"Successfully read {gcs_df.count()} records from GCS")
    gcs_df.show(5)

    # Test reading from S3
    print("Reading from s3 bucket....."
    s3_df = spark.read.option('inferSchema','true').option('header','true').csv("s3a://homelab-data-lake/dev/games/df_games_and_open.csv")
    print(f"Successfully read {s3_df.count()} records from GCS")
    s3_df.show(5)

except Exception as e:
    print(f"Error accessing GCS: {str(e)}")
spark.stop()

```

## submit the code

```bash
spark-submit test-storage-connectivity.py
```