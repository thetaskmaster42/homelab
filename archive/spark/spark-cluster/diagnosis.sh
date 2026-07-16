#!/bin/bash
# diagnose-resources.sh - Diagnose Spark resource allocation issues

MASTER_URL="spark://server.lan:7077"
MASTER_WEB_UI="http://server.lan:8080"

echo "=== Spark Resource Allocation Diagnostic ==="
echo ""

echo "1. Checking Master Status..."
MASTER_PROC=$(jps -lm | grep Master)
if [ -n "$MASTER_PROC" ]; then
    echo "   ✓ Master is running"
    echo "   $MASTER_PROC"
else
    echo "   ✗ Master is NOT running"
    echo "   FIX: /opt/spark/sbin/start-master.sh"
    exit 1
fi
echo ""

echo "2. Checking Worker Status..."
WORKER_COUNT=0
for worker in pi4 pi2 pi3; do
    WORKER_PROC=$(ssh $worker jps -lm 2>/dev/null | grep Worker)
    if [ -n "$WORKER_PROC" ]; then
        echo "   ✓ $worker is running"
        WORKER_COUNT=$((WORKER_COUNT + 1))
    else
        echo "   ✗ $worker is NOT running"
        echo "     FIX: ssh $worker /opt/spark/sbin/start-worker.sh $MASTER_URL"
    fi
done
echo "   Total workers running: $WORKER_COUNT / 3"
echo ""

echo "3. Checking Registered Workers via Master UI..."
# Requires curl and jq
if command -v curl &> /dev/null && command -v jq &> /dev/null; then
    ALIVE_WORKERS=$(curl -s $MASTER_WEB_UI/json/ | jq '.aliveworkers')
    CORES=$(curl -s $MASTER_WEB_UI/json/ | jq '.cores')
    CORES_USED=$(curl -s $MASTER_WEB_UI/json/ | jq '.coresused')
    MEMORY=$(curl -s $MASTER_WEB_UI/json/ | jq '.memory')
    MEMORY_USED=$(curl -s $MASTER_WEB_UI/json/ | jq '.memoryused')
    
    echo "   Alive Workers: $ALIVE_WORKERS"
    echo "   Total Cores: $CORES (Used: $CORES_USED, Free: $((CORES - CORES_USED)))"
    echo "   Total Memory: $MEMORY MB (Used: $MEMORY_USED MB, Free: $((MEMORY - MEMORY_USED)) MB)"
else
    echo "   Install curl and jq for detailed stats"
    echo "   Or check manually: $MASTER_WEB_UI"
fi
echo ""

echo "4. Checking Worker Resources..."
for worker in pi4 pi2 pi3; do
    echo "   $worker:"
    ssh $worker "grep -E 'SPARK_WORKER_CORES|SPARK_WORKER_MEMORY' /opt/spark/conf/spark-env.sh | grep -v '^#'" 2>/dev/null
done
echo ""

echo "5. Checking spark-defaults.conf..."
if [ -f "/opt/spark/conf/spark-defaults.conf" ]; then
    echo "   Executor Configuration:"
    grep -E "executor.cores|executor.memory|executor.instances|dynamicAllocation" /opt/spark/conf/spark-defaults.conf | grep -v "^#"
else
    echo "   spark-defaults.conf not found"
fi
echo ""

echo "6. Checking for Running Applications..."
if command -v curl &> /dev/null && command -v jq &> /dev/null; then
    RUNNING_APPS=$(curl -s $MASTER_WEB_UI/json/ | jq '.activeapps | length')
    echo "   Running Applications: $RUNNING_APPS"
    if [ "$RUNNING_APPS" -gt 0 ]; then
        echo "   Application IDs:"
        curl -s $MASTER_WEB_UI/json/ | jq -r '.activeapps[].id'
    fi
else
    echo "   Check manually: $MASTER_WEB_UI"
fi
echo ""

echo "=== Recommendations ==="
if [ "$WORKER_COUNT" -lt 3 ]; then
    echo "⚠ Start missing workers:"
    echo "  /opt/spark/sbin/start-workers.sh"
fi

if [ "$WORKER_COUNT" -eq 3 ]; then
    echo "✓ All workers are running"
    echo ""
    echo "Recommended Spark Configuration for your cluster:"
    echo "  --executor-cores 3           (or 2 for safety)"
    echo "  --executor-memory 10g        (or 8g for safety)"
    echo "  --num-executors 3"
    echo ""
    echo "Python Example:"
    echo "  spark = SparkSession.builder \\"
    echo "      .master('$MASTER_URL') \\"
    echo "      .config('spark.executor.cores', '3') \\"
    echo "      .config('spark.executor.memory', '10g') \\"
    echo "      .config('spark.executor.instances', '3') \\"
    echo "      .getOrCreate()"
fi

echo ""
echo "View Master UI: $MASTER_WEB_UI"