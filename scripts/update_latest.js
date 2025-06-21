const fs = require('fs');
const path = require('path');
const { createClient } = require('@supabase/supabase-js');

// Read from env file
const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_ROLE_KEY
);

const inputFile = path.join(__dirname, '../data/ais_latest.json');
const outputFile = path.join(__dirname, '../data/ais_latest.json');

async function main() {
  console.log('🔍 Loading MMSI list from ais_latest.json...');
  const mmsiList = JSON.parse(fs.readFileSync(inputFile, 'utf-8')).map(ship => ship.mmsi);

  console.log('🛰️  Querying Supabase for latest records...');
  const { data, error } = await supabase
    .from('ais_logs')
    .select('id, name, mmsi, lat, lon, cog, sog, timestamp')
    .in('mmsi', mmsiList)
    .order('timestamp', { ascending: false });

  if (error) {
    console.error('❌ Failed to fetch from Supabase:', error.message);
    process.exit(1);
  }

  const latestMap = new Map();
  const latestRecords = [];

  for (const row of data) {
    if (!latestMap.has(row.mmsi)) {
      latestMap.set(row.mmsi, row);
      latestRecords.push(row);
    }
  }

  console.log(`📦 Found ${latestRecords.length} latest records. Writing to JSON...`);

  fs.writeFileSync(outputFile, JSON.stringify(latestRecords, null, 2));

  // Delete old records
  const keepIds = latestRecords.map(r => r.id);

  console.log(`🧹 Deleting ${data.length - keepIds.length} old records from Supabase...`);
  const { error: deleteError } = await supabase
    .from('ais_logs')
    .delete()
    .not('id', 'in', keepIds);

  if (deleteError) {
    console.error('❌ Failed to delete old records:', deleteError.message);
    process.exit(1);
  }

  console.log('✅ Update complete.');
}

main();
