from colorama import Fore, Back, Style


def print_error(message: str):
    """打印红色错误信息"""
    print(f"{Fore.RED}[ERROR] {message}{Style.RESET_ALL}")


def print_warn(message: str):
    """打印黄色警告信息"""
    print(f"{Fore.YELLOW}[WARN] {message}{Style.RESET_ALL}")


def print_success(message: str):
    """打印绿色成功信息"""
    print(f"{Fore.GREEN}[OK] {message}{Style.RESET_ALL}")


def print_info(message: str):
    """打印蓝色信息"""
    print(f"{Fore.BLUE}[INFO] {message}{Style.RESET_ALL}")


# 可选：带背景色
def print_highlight(message: str):
    print(f"{Back.CYAN}{Fore.BLACK}{message}{Style.RESET_ALL}")
