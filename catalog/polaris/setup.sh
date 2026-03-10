git clone https://github.com/apache/polaris.git
cd polaris

# building polaris artifacts
./gradlew \
  :polaris-server:assemble \
  :polaris-server:quarkusAppPartsBuild --rerun \
  :polaris-admin:assemble \
  :polaris-admin:quarkusAppPartsBuild --rerun \
  -Dquarkus.container-image.build=true



export ASSETS_PATH=$(pwd)/getting-started/assets/
export QUARKUS_DATASOURCE_JDBC_URL=jdbc:postgresql://ubuntu:5432/POLARIS
export QUARKUS_DATASOURCE_USERNAME=postgres
export QUARKUS_DATASOURCE_PASSWORD=postgres
export CLIENT_ID=root
export CLIENT_SECRET=s3cr3t

docker compose -p polaris \
  -f getting-started/jdbc/docker-compose-bootstrap-db.yml \
  -f getting-started/jdbc/docker-compose.yml up -d

#   -f getting-started/assets/postgres/docker-compose-postgres.yml \