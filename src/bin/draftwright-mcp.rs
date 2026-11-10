fn main() -> std::process::ExitCode {
    draftwright::cli::mcp::main(std::env::args().collect()).into()
}
