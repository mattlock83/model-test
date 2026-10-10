use std::io::{self, BufReader, BufWriter};
fn main() {
    let args: Vec<_> = std::env::args().collect();
    if args.iter().any(|a| a == "--version") {
        println!("testwalker-core {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if args.iter().any(|a| a == "--help" || a == "-h") {
        println!(
            "testwalker-core [--version]\nJSON-RPC 2.0 worker: newline-delimited UTF-8 requests on stdin, responses on stdout.\nUse the testwalker Python CLI for web testing. See docs/protocol.md."
        );
        return;
    }
    let mut peer = testwalker_core::protocol::Peer::new(
        BufReader::new(io::stdin().lock()),
        BufWriter::new(io::stdout().lock()),
    );
    if let Err(error) = peer.serve() {
        eprintln!("testwalker-core: {error}");
        std::process::exit(2);
    }
}
