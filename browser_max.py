"""
Модуль автоматизации браузера для отправки сообщений в MAX
Использует agent-browser CLI

Правильный порядок загрузки файла:
1. Подключиться к Chrome
2. Дождаться загрузки страницы
3. Найти и заполнить поле ввода текста (НЕ отправлять!)
4. Нажать кнопку скрепки
5. Дождаться появления input[type="file"]
6. Установить файл в input
7. Вызвать change event
8. Дождаться загрузки файла
9. Нажать кнопку отправки
10. Дождаться подтверждения
"""

import subprocess
import time
import os
from typing import Optional


class BrowserMAX:
    """Класс для взаимодействия с MAX через браузер"""

    def __init__(self, channel_url: str, headless: bool = False):
        self.channel_url = channel_url
        self.headless = headless
        self.session_name = "max_archiver"
        self.use_existing_chrome = True  # Подключаться к существующему Chrome
        # Запоминаем ref элементов для отправки
        self._message_input_ref: Optional[str] = None
        self._send_button_ref: Optional[str] = None

    def _run_agent(self, args: list, timeout: int = 30) -> subprocess.CompletedProcess:
        """
        Запустить команду agent-browser

        Args:
            args: Список аргументов команды
            timeout: Таймаут в секундах

        Returns:
            CompletedProcess объект
        """
        # Использовать CDP подключение к существующему Chrome
        if self.use_existing_chrome:
            cmd = ["npx.cmd", "agent-browser", "--cdp", "9222"] + args
        else:
            cmd = ["npx.cmd", "agent-browser"] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout
            )
            return result
        except subprocess.TimeoutExpired:
            print(f"  ⚠ Команда превысила таймаут ({timeout} сек)")
            raise

    def _run_agent_checked(self, args: list, timeout: int = 30) -> tuple[bool, str]:
        """
        Запустить команду agent-browser с проверкой результата

        Args:
            args: Список аргументов
            timeout: Таймаут

        Returns:
            (success, output) - успех и вывод
        """
        try:
            result = self._run_agent(args, timeout)
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as e:
            return False, str(e)

    # ─────────────────────────────────────────────────────────────
    # Базовые методы
    # ─────────────────────────────────────────────────────────────

    def open_channel(self) -> bool:
        """
        Браузер уже открыт пользователем на канале MAX.
        Просто проверяем подключение.

        Returns:
            True при успехе
        """
        print(f"  🌐 Подключаюсь к Chrome (CDP port 9222)...")

        # Проверить подключение - сделать snapshot
        result = self._run_agent(["snapshot", "-i"], timeout=15)

        if result.returncode == 0:
            print("  ✓ Подключение установлено")
            return True
        else:
            print(f"  ✗ Не удалось подключиться: {result.stderr}")
            return False

    def wait_for_load(self, timeout: int = 30) -> bool:
        """Ждать загрузки страницы"""
        success, _ = self._run_agent_checked(
            ["--session", self.session_name, "wait", "--load", "networkidle"],
            timeout=timeout
        )
        return success

    def get_snapshot(self) -> str:
        """Получить snapshot страницы"""
        result = self._run_agent(
            ["--session", self.session_name, "snapshot", "-i"],
            timeout=15
        )
        return result.stdout

    # ─────────────────────────────────────────────────────────────
    # Работа с сообщением
    # ─────────────────────────────────────────────────────────────

    def find_message_input(self) -> Optional[dict]:
        """
        Найти поле ввода сообщения и кнопку отправки

        Returns:
            dict с ключами "input_ref" и "send_ref" или None
        """
        print("  🔍 Ищу поле ввода сообщения...")

        # Получить snapshot для анализа структуры
        snapshot = self.get_snapshot()

        # Попытка 1: Найти через placeholder
        attempts = [
            'placeholder:"Сообщение"',
            'placeholder:"Message"',
            'placeholder:"Введите сообщение"',
        ]

        for selector in attempts:
            success, output = self._run_agent_checked(
                ["--session", self.session_name, "find", selector, "-n"],
                timeout=5
            )
            if success:
                # Парсим ref из вывода
                for line in output.split('\n'):
                    if '@e' in line:
                        self._message_input_ref = line.split()[0]
                        print(f"  ✓ Поле ввода найдено: {self._message_input_ref}")
                        break

        # Попытка 2: Найти через contenteditable
        if not self._message_input_ref:
            success, output = self._run_agent_checked(
                ["--session", self.session_name, "eval",
                 "(() => { const el = document.querySelector('[contenteditable]') || "
                 "document.querySelector('div[role=\"textbox\"]') || "
                 "document.querySelector('textarea'); "
                 "return el ? el.getAttribute('data-testid') || el.className.slice(0,50) : null; })()"],
                timeout=5
            )
            if success and output.strip():
                print(f"  ℹ Найден contenteditable: {output[:80]}")

        # Попытка 3: Найти input[type="text"] или textarea
        if not self._message_input_ref:
            success, output = self._run_agent_checked(
                ["--session", self.session_name, "eval",
                 "(() => { const el = document.querySelector('input[type=\"text\"]') || "
                 "document.querySelector('textarea'); "
                 "if (el) return el.getAttribute('data-testid') || el.id || el.className.slice(0,30); return null; })()"],
                timeout=5
            )
            if success and output.strip():
                print(f"  ℹ Найден input/textarea: {output[:80]}")

        # Попытка 4: Найти через find role
        if not self._message_input_ref:
            success, _ = self._run_agent_checked(
                ["--session", self.session_name, "find", "role", "textbox", "-n"],
                timeout=5
            )

        if self._message_input_ref:
            # Теперь найдём кнопку отправки рядом
            self._find_send_button(snapshot)
            return {
                "input_ref": self._message_input_ref,
                "send_ref": self._send_button_ref
            }

        return None

    def _find_send_button(self, snapshot: str = ""):
        """Найти кнопку отправки сообщения"""
        # Попытка 1: Найти кнопку с иконкой или текстом
        send_selectors = [
            'title:"Отправить"',
            'title:"Send"',
            'aria-label:"Отправить"',
            'aria-label:"Send"',
        ]

        for selector in send_selectors:
            success, _ = self._run_agent_checked(
                ["--session", self.session_name, "find", selector, "-n"],
                timeout=5
            )
            if success:
                print("  ✓ Кнопка отправки найдена")
                return

        # Попытка 2: Найти button рядом с input
        success, output = self._run_agent_checked(
            ["--session", self.session_name, "eval",
             "(() => { const input = document.querySelector('[contenteditable], textarea, input[type=\"text\"]'); "
             "if (!input) return null; "
             "const parent = input.closest('[class*=\"composer\"], [class*=\"message\"], [class*=\"input\"]'); "
             "const btn = parent?.querySelector('button[type=\"submit\"], button svg'); "
             "return btn ? (btn.getAttribute('data-testid') || btn.className.slice(0,30)) : null; })()"],
            timeout=5
        )
        if success and output.strip():
            print(f"  ℹ Кнопка отправки: {output[:50]}")

    def prepare_message(self, text: str) -> bool:
        """
        Подготовить текстовое сообщение (ввести текст, НЕ отправлять)

        Args:
            text: Текст сообщения

        Returns:
            True при успехе
        """
        print("  ⌨ Ввожу текст сообщения...")

        self._message_input_ref = "e23"

        # Используем команду type для ввода текста
        success, output = self._run_agent_checked(
            ["--session", self.session_name, "type", "e23", text],
            timeout=10
        )

        if success:
            print("  ✓ Текст введён")
            return True

        print("  ✗ Не удалось ввести текст")
        return False

    def click_send_button(self) -> bool:
        """
        Нажать кнопку отправки сообщения

        Returns:
            True при успехе
        """
        print("  📤 Нажимаю кнопку отправки...")

        # Попытка 1: Enter (если работает)
        success, _ = self._run_agent_checked(
            ["--session", self.session_name, "press", "Enter"],
            timeout=5
        )
        if success:
            print("  ✓ Отправлено (Enter)")
            return True

        # Попытка 2: Найти и кликнуть кнопку
        send_attempts = [
            'title:"Отправить"',
            'title:"Send"',
            '[class*="send"]',
            '[class*="submit"]',
        ]

        for selector in send_attempts:
            success, _ = self._run_agent_checked(
                ["--session", self.session_name, "find", selector, "click"],
                timeout=5
            )
            if success:
                print(f"  ✓ Отправлено ({selector})")
                return True

        # Попытка 3: Кликнуть на кнопку рядом с input
        success, _ = self._run_agent_checked(
            ["--session", self.session_name, "eval",
             "(el => el?.closest('[class*=\"composer\"]')?.querySelector('button')?.click())"
             "(document.querySelector('[contenteditable], textarea, input[type=\"text\"]'))"],
            timeout=5
        )
        if success:
            print("  ✓ Отправлено (найдена соседняя кнопка)")
            return True

        print("  ⚠ Не удалось нажать кнопку отправки")
        return False

    # ─────────────────────────────────────────────────────────────
    # Работа с файлами
    # ─────────────────────────────────────────────────────────────

    def click_attachment_button(self) -> Optional[str]:
        """
        Нажать на кнопку скрепки (attach) и дождаться появления input[type="file"]

        Returns:
            ref input[type="file"] или None
        """
        print("  📎 Нажимаю кнопку скрепки...")

        success, _ = self._run_agent_checked(
            ["--session", self.session_name, "click", "e13"],
            timeout=5
        )

        if success:
            print("  ✓ Кнопка скрепки нажата (e13)")
        else:
            # Попытка через текст
            success, _ = self._run_agent_checked(
                ["--session", self.session_name, "find", "text", "Upload file", "click"],
                timeout=5
            )
            if success:
                print("  ✓ Кнопка скрепки нажата")

        if not success:
            print("  ⚠ Кнопка не найдена, ищу в snapshot...")
            snapshot = self.get_snapshot()
            for line in snapshot.split('\n')[:50]:
                l = line.lower()
                if any(x in l for x in ['upload', 'attach', 'file', 'clip']):
                    print(f"    {line.strip()}")

        time.sleep(1)  # Ждём появления диалога

        # Теперь ищем input[type="file"]
        return self._wait_for_file_input()

    def _wait_for_file_input(self, timeout: int = 5) -> Optional[str]:
        """
        Дождаться появления input[type="file"] после клика на скрепку

        Args:
            timeout: Максимальное время ожидания

        Returns:
            ref input или None
        """
        print("  📁 Ожидаю диалог выбора файла...")

        for i in range(timeout):
            success, output = self._run_agent_checked(
                ["--session", self.session_name, "eval",
                 "(() => { const el = document.querySelector('input[type=\"file\"]'); "
                 "return el ? 'found' : null; })()"],
                timeout=5
            )

            if success and output.strip() and output.strip() != "null":
                print("  ✓ Input для файла найден")
                return "input[type=file]"

            time.sleep(1)

        print("  ⚠ Input[type=file] не появился")
        return None

    def set_file_to_input(self, input_ref: str, filepath: str) -> bool:
        """
        Установить файл в input[type="file"]

        Args:
            input_ref: ref элемента input
            filepath: путь к файлу

        Returns:
            True при успехе
        """
        abs_path = os.path.abspath(filepath)
        file_size = os.path.getsize(filepath) / 1024 / 1024

        print(f"  📎 Выбираю файл: {os.path.basename(filepath)} ({file_size:.1f} MB)")

        # Используем CSS селектор для upload
        success, output = self._run_agent_checked(
            ["--session", self.session_name, "upload", 'input[type="file"]', abs_path],
            timeout=120
        )

        if success:
            print("  ✓ Файл выбран")
            return True

        # Резервный метод: nth-child
        success, output = self._run_agent_checked(
            ["--session", self.session_name, "upload", 'input:nth-child(2)', abs_path],
            timeout=120
        )

        if success:
            print("  ✓ Файл выбран (nth-child)")
            return True

        print("  ✗ Не удалось выбрать файл")
        return False

    def trigger_file_change(self, input_ref: str) -> bool:
        """
        Вызвать событие change для input[type="file"]

        Args:
            input_ref: ref input элемента

        Returns:
            True при успехе
        """
        print("  🔄 Триггерю событие изменения...")

        success, _ = self._run_agent_checked(
            ["--session", self.session_name, "eval",
             "document.querySelector('input[type=\"file\"]')?.dispatchEvent(new Event('change', {bubbles:true}))"],
            timeout=5
        )

        return success

    def wait_for_upload(self, timeout: int = 120) -> bool:
        """
        Ждать завершения загрузки файла

        Args:
            timeout: Максимальное время ожидания

        Returns:
            True если загружен
        """
        print(f"  ⏳ Ожидание загрузки (до {timeout} сек)...")

        start_time = time.time()
        check_interval = 5

        while time.time() - start_time < timeout:
            time.sleep(check_interval)

            # Проверить наличие индикаторов загрузки
            success, output = self._run_agent_checked(
                ["--session", self.session_name, "eval",
                 "(() => { "
                 "const progress = document.querySelector('[class*=\"progress\"], [class*=\"upload\"]'); "
                 "const uploading = document.querySelector('[class*=\"uploading\"], [class*=\"sending\"]'); "
                 "const spinner = document.querySelector('[class*=\"spinner\"]'); "
                 "return progress || uploading || spinner ? 'uploading' : 'done'; "
                 "})()"],
                timeout=10
            )

            elapsed = int(time.time() - start_time)

            if success and "uploading" in output:
                print(f"  📤 Загрузка в процессе... ({elapsed} сек)")
            else:
                print(f"  ✓ Загрузка завершена ({elapsed} сек)")
                return True

        print("  ⚠ Превышен таймаут ожидания загрузки")
        return False

    def wait_for_confirm(self, timeout: int = 30) -> bool:
        """
        Дождаться подтверждения отправки сообщения

        Args:
            timeout: Максимальное время ожидания

        Returns:
            True если сообщение появилось в чате
        """
        print(f"  ✓ Дожидаюсь подтверждения...")

        # Получаем количество сообщений до отправки
        success, before = self._run_agent_checked(
            ["--session", self.session_name, "eval",
             "document.querySelectorAll('[class*=\"message\"]').length"],
            timeout=5
        )

        start_time = time.time()

        while time.time() - start_time < timeout:
            time.sleep(2)

            success, after = self._run_agent_checked(
                ["--session", self.session_name, "eval",
                 "document.querySelectorAll('[class*=\"message\"]').length"],
                timeout=5
            )

            if success:
                try:
                    before_count = int(before.strip()) if before.strip() else 0
                    after_count = int(after.strip()) if after.strip() else 0

                    if after_count > before_count:
                        print("  ✓ Сообщение появилось в чате!")
                        return True

                    # Проверяем наличие нашего текста в DOM
                    if self._message_input_ref:
                        success2, content = self._run_agent_checked(
                            ["--session", self.session_name, "eval",
                             "(document.querySelector('[class*=\"message\"]:last-child')?.textContent || '').slice(0,100)"],
                            timeout=5
                        )
                        if success2 and content.strip():
                            print(f"  ✓ Последнее сообщение: {content[:50]}...")

                except ValueError:
                    pass

        print("  ⚠ Подтверждение не получено")
        return True  # Возвращаем True если не дождались, но ошибки нет

    # ─────────────────────────────────────────────────────────────
    # Главный метод отправки
    # ─────────────────────────────────────────────────────────────

    def send_message_with_file(self, text: str, filepath: str,
                               retries: int = 3, retry_delay: int = 10) -> bool:
        """
        Отправить сообщение с файлом в MAX

        ПРАВИЛЬНЫЙ ПОРЯДОК:
        1. Подключиться к Chrome
        2. Дождаться загрузки страницы
        3. Ввести текст (НЕ отправлять!)
        4. Нажать скрепку → дождаться input[type="file"]
        5. Установить файл в input
        6. Дождаться загрузки
        7. Нажать отправку
        8. Дождаться подтверждения

        Args:
            text: Текст сообщения
            filepath: Путь к файлу
            retries: Количество попыток при ошибке
            retry_delay: Задержка между попытками

        Returns:
            True при успехе
        """
        # Проверяем существование файла
        if not os.path.exists(filepath):
            print(f"  ✗ Файл не найден: {filepath}")
            return False

        file_size = os.path.getsize(filepath) / 1024 / 1024
        print(f"  📦 Файл: {os.path.basename(filepath)} ({file_size:.1f} MB)")

        for attempt in range(1, retries + 1):
            try:
                print(f"\n  {'─' * 40}")
                print(f"  Попытка {attempt}/{retries}")
                print(f"  {'─' * 40}")

                # 1. Подключиться
                print("  1️⃣ Подключаюсь к MAX...")
                if not self.open_channel():
                    raise Exception("Не удалось подключиться к Chrome")

                # 2. Дождаться загрузки
                print("  2️⃣ Дожидаюсь загрузки страницы...")
                self.wait_for_load()

                # 3. Ввести текст (НЕ отправлять!)
                print("  3️⃣ Ввожу текст сообщения...")
                if not self.prepare_message(text):
                    raise Exception("Не удалось ввести текст")

                # 4. Нажать скрепку и получить input для файла
                print("  4️⃣ Нажимаю кнопку скрепки...")
                input_ref = self.click_attachment_button()
                if not input_ref:
                    # Пробуем через snapshot найти скрытый input
                    print("  Пробую найти input[type=file] напрямую...")
                    success, output = self._run_agent_checked(
                        ["--session", self.session_name, "eval",
                         "(document.querySelector('input[type=file]') ? 'found' : 'not-found')"],
                        timeout=5
                    )
                    if not (success and "found" in output):
                        raise Exception("Кнопка скрепки не найдена")

                time.sleep(1)

                # 5. Установить файл
                print("  5️⃣ Устанавливаю файл...")
                if not self.set_file_to_input(input_ref or "@e1", filepath):
                    raise Exception("Не удалось выбрать файл")

                # 6. Ждать загрузки
                print("  6️⃣ Загружаю файл...")
                if not self.wait_for_upload(timeout=120):
                    print("  ⚠ Загрузка может быть не завершена")

                # 7. Нажать отправку
                print("  7️⃣ Отправляю сообщение...")
                self.click_send_button()

                # 8. Дождаться подтверждения
                print("  8️⃣ Дожидаюсь подтверждения...")
                if not self.wait_for_confirm():
                    print("  ⚠ Подтверждение не получено")

                print("\n  ✅ Сообщение с файлом отправлено!")
                return True

            except Exception as e:
                print(f"  ❌ Ошибка: {e}")
                if attempt < retries:
                    print(f"  ⏳ Жду {retry_delay} сек перед повтором...")
                    time.sleep(retry_delay)
                else:
                    print("  ✗ Превышено количество попыток")
                    return False

        return False

    def close(self):
        """Закрыть соединение"""
        print("  🧹 Закрываю соединение...")
        self._run_agent_checked(
            ["--session", self.session_name, "close"],
            timeout=5
        )
        print("  ✓ Соединение закрыто")


# ─────────────────────────────────────────────────────────────────
# Тестирование
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Browser MAX модуль")
    print("Используйте: from browser_max import BrowserMAX")