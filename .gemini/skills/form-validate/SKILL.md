---
name: form-validate
description: Валидация управляемой формы 1С. Используй после создания или модификации формы для проверки корректности. При наличии BaseForm автоматически проверяет callType и ID расширений
argument-hint: <FormPath> [-Detailed] [-MaxErrors 30]
allowed-tools:
  - Bash
  - Read
  - Glob
---

# /form-validate — валидация управляемой формы 1С

Проверяет Form.xml на структурные ошибки: уникальность ID, наличие companion-элементов, корректность ссылок DataPath и команд.

Для добавленных в extension-форму произвольных `DynamicList` дополнительно проверяется устойчивость ключа и жизненный цикл. Видимый параметризованный список должен получить все параметры в `OnCreateAtServer`; альтернатива — начально скрыть таблицу/группу и показать её только после установки параметров. Для флажка, который управляет такой панелью, валидатор ищет клиентский early-return до локального вызова, достигающего серверной процедуры. Это эвристика по локальному call graph, а не замена runtime-проверки.

## Параметры

| Параметр  | Обяз. | Умолч. | Описание                                |
|-----------|:-----:|---------|-----------------------------------------|
| FormPath  | да    | —       | Путь к файлу Form.xml                   |
| Detailed  | нет   | —       | Подробный вывод (все проверки, включая успешные) |
| MaxErrors | нет   | 30      | Остановиться после N ошибок              |

## Команда

```powershell
powershell.exe -NoProfile -File ".gemini/skills/form-validate/scripts/form-validate.ps1" -FormPath "Catalogs/Номенклатура/Forms/ФормаЭлемента"
powershell.exe -NoProfile -File ".gemini/skills/form-validate/scripts/form-validate.ps1" -FormPath "src/МояОбработка/Forms/Форма/Ext/Form.xml"
```
