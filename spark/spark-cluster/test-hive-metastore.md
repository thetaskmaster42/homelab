# using shell
```bash
pyspark --conf spark.sql.catalogImplementation=hive
```

## Note: Check if hive thrift server is running,if not run the below
```
hive --service metastore
```

## Using HMS in spark 
### Hive Metastore converts the query to sql from spark via Thrift server

```
pyspark \
--master spark://ubuntu:7077 \
--deploy-mode client \
--conf spark.app.name=HiveMetastoreTest \
--conf spark.sql.catalog.metastore.type=hive 


spark.sql('create database test_db');
spark.sql('show databases').show();

query = 'create table t1(id int, name string)'
spark.sql(query)

insert_query = 'insert into t1 value(1,"test")'
spark.sql(insert_query)

spark.sql('select * from t1').show()
+---+----+
| id|name|
+---+----+
|  1|test|
+---+----+
```

## Verification on Postgres database
```sql
$ psql -U postgres -h ubuntu -d metastore
select * from "TBLS" where tablename="t1";
```

## On s3
![alt text](../../../images/spark-insert-hive-table.png)

## Test Iceberg read and write with HMS

WIP
```
pyspark \
--master spark://ubuntu:7077 \
--deploy-mode client \
--conf spark.app.name=HomelabSparkApp \
--conf spark.sql.catalog.metastore.type=hive \
--packages org.apache.iceberg:iceberg-spark-runtime-4.0_2.13:1.10.1 \
--conf spark.sql.catalog.my_catalog=org.apache.iceberg.spark.SparkCatalog \
--conf spark.sql.catalog.my_catalog.type=hive \
--conf spark.sql.catalog.my_catalog.uri=thrift://ubuntu:9083 \
--conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions
```
