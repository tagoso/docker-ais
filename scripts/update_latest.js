const fs = require('fs');
const path = require('path');
const { createClient } = require('@supabase/supabase-js');

// Init Supabase
const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_ROLE_KEY
);

const inputFile = path.join(__dirname, '../data/ais_latest.json');
const outputFile = path.join(__dirname, '../data/ais_latest.json');

async function main() {
  console.log('🔍 Loading MMSI list from ais_latest.json...');
  const existingData = JSON.parse(fs.readFileSync(inputFile, 'utf-8'));
  const mmsiList = existingData.map(ship => ship.mmsi);

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

  // Latest data (1 set per MMSI)
  const latestMap = new Map();
  for (const row of data) {
    if (!latestMap.has(row.mmsi)) {
      latestMap.set(row.mmsi, row);
    }
  }

  // Overwrite the existing data
  const merged = existingData.map(ship => {
    return latestMap.has(ship.mmsi) ? latestMap.get(ship.mmsi) : ship;
  });

  console.log(`📦 Merged ${merged.length} records. Writing to JSON...`);
  fs.writeFileSync(outputFile, JSON.stringify(merged, null, 2));

  // Step 1: Backup to ais_archive
  console.log('📁 Backing up all records to ais_archive...');
  const { data: allLogs, error: fetchAllError } = await supabase
    .from('ais_logs')
    .select('*');

  if (fetchAllError) {
    console.error('❌ Failed to fetch all ais_logs for archive:', fetchAllError.message);
    process.exit(1);
  }

  if (allLogs.length > 0) {
    const { error: archiveError } = await supabase
      .from('ais_archive')
      .insert(allLogs);

    if (archiveError) {
      console.error('❌ Failed to archive records:', archiveError.message);
      process.exit(1);
    }
  } else {
    console.log('⚠️ No records found to archive.');
  }

  // Step 2: Delete ALL records from ais_logs
  console.log('🧹 Deleting ALL records from ais_logs table...');
  const { error: deleteError } = await supabase
    .from('ais_logs')
    .delete()
    .not('id', 'is', null);

  if (deleteError) {
    console.error('❌ Failed to delete records:', deleteError.message);
    process.exit(1);
  }

  console.log('✅ Update and archive complete.');
}

main();
