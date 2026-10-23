use std::env;
use std::convert::TryInto;
use std::fs::{self, File};
use std::io::{self, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

const SOURCE_MAGIC: &[u8; 8] = b"AHCDV1\n\0";
const INDEX_MAGIC: &[u8; 8] = b"AHIDXV1\0";
const RECORD_SIZE: u64 = 48;
const BLOCK_RECORDS: u64 = 100_000;
const INDEX_RECORD_SIZE: usize = 32;

#[derive(Clone, Copy)]
struct Block {
    start: u64,
    count: u64,
    min_slot: u64,
    max_slot: u64,
}

fn read_u64(bytes: &[u8]) -> u64 {
    u64::from_le_bytes(bytes.try_into().unwrap())
}

fn read_existing(path: &Path) -> io::Result<Vec<Block>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let mut reader = BufReader::new(File::open(path)?);
    let mut header = [0u8; 24];
    reader.read_exact(&mut header)?;
    if &header[..8] != INDEX_MAGIC || read_u64(&header[8..16]) != RECORD_SIZE || read_u64(&header[16..24]) != BLOCK_RECORDS {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "compact index header mismatch"));
    }
    let mut blocks = Vec::new();
    loop {
        let mut raw = [0u8; INDEX_RECORD_SIZE];
        match reader.read_exact(&mut raw) {
            Ok(()) => blocks.push(Block {
                start: read_u64(&raw[0..8]), count: read_u64(&raw[8..16]),
                min_slot: read_u64(&raw[16..24]), max_slot: read_u64(&raw[24..32]),
            }),
            Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => break,
            Err(error) => return Err(error),
        }
    }
    Ok(blocks)
}

fn write_index(path: &Path, blocks: &[Block]) -> io::Result<()> {
    let temporary = PathBuf::from(format!("{}.tmp", path.display()));
    let mut writer = BufWriter::new(File::create(&temporary)?);
    writer.write_all(INDEX_MAGIC)?;
    writer.write_all(&RECORD_SIZE.to_le_bytes())?;
    writer.write_all(&BLOCK_RECORDS.to_le_bytes())?;
    for block in blocks {
        writer.write_all(&block.start.to_le_bytes())?;
        writer.write_all(&block.count.to_le_bytes())?;
        writer.write_all(&block.min_slot.to_le_bytes())?;
        writer.write_all(&block.max_slot.to_le_bytes())?;
    }
    writer.flush()?;
    writer.get_ref().sync_all()?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn main() -> io::Result<()> {
    let arguments: Vec<String> = env::args().collect();
    if arguments.len() != 3 {
        eprintln!("usage: {} COMPACT_JOURNAL INDEX", arguments[0]);
        std::process::exit(2);
    }
    let source_path = Path::new(&arguments[1]);
    let index_path = Path::new(&arguments[2]);
    let source_size = source_path.metadata()?.len();
    if source_size < SOURCE_MAGIC.len() as u64 {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "compact journal too short"));
    }
    let total_records = (source_size - SOURCE_MAGIC.len() as u64) / RECORD_SIZE;
    let mut blocks = read_existing(index_path)?;
    if blocks.last().map(|block| block.count < BLOCK_RECORDS).unwrap_or(false) {
        blocks.pop();
    }
    let indexed_records = blocks.last().map(|block| block.start + block.count).unwrap_or(0);
    if indexed_records > total_records {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "compact journal shrank"));
    }

    let mut reader = BufReader::with_capacity(8 * 1024 * 1024, File::open(source_path)?);
    let mut magic = [0u8; 8];
    reader.read_exact(&mut magic)?;
    if &magic != SOURCE_MAGIC {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "compact journal magic mismatch"));
    }
    reader.seek(SeekFrom::Start(SOURCE_MAGIC.len() as u64 + indexed_records * RECORD_SIZE))?;
    let mut start = indexed_records;
    let mut buffer = vec![0u8; (BLOCK_RECORDS * RECORD_SIZE) as usize];
    while start < total_records {
        let count = (total_records - start).min(BLOCK_RECORDS);
        let bytes = (count * RECORD_SIZE) as usize;
        reader.read_exact(&mut buffer[..bytes])?;
        let mut min_slot = u64::MAX;
        let mut max_slot = 0u64;
        for record in buffer[..bytes].chunks_exact(RECORD_SIZE as usize) {
            let slot = read_u64(&record[40..48]);
            min_slot = min_slot.min(slot);
            max_slot = max_slot.max(slot);
        }
        blocks.push(Block { start, count, min_slot, max_slot });
        start += count;
    }
    write_index(index_path, &blocks)?;
    println!("indexed_records={} blocks={} source_bytes={}", total_records, blocks.len(), source_size);
    Ok(())
}
