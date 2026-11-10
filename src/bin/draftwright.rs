fn main() -> std::process::ExitCode {
    draftwright::cli::main(std::env::args_os().collect()).into()
}
