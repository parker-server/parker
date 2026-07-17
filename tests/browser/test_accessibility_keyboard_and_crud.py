import pytest


@pytest.mark.browser
def test_ctrl_k_focuses_search_and_escape_closes_quick_results(page, browser_server):
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")

    page.keyboard.press("Control+k")
    page.wait_for_function(
        """
        () => {
            const input = document.querySelector('nav input[type="search"]');
            return document.activeElement === input;
        }
        """
    )

    search_input = page.locator("nav input[type='search']").first
    search_input.fill("Crossover")
    result_link = page.get_by_role("link", name=browser_server["seed"]["reading_list_name"]).first
    result_link.wait_for()

    search_input.press("Escape")
    page.wait_for_function(
        """
        () => {
            const quickSearch = [...document.querySelectorAll('[x-data="quickSearch()"]')]
                .find((el) => el.offsetParent !== null);
            const state = quickSearch?._x_dataStack?.[0];
            return state?.query === 'Crossover' && state?.isOpen === false;
        }
        """
    )


@pytest.mark.browser
def test_reader_goto_modal_focuses_input_and_submits_with_keyboard(page, browser_server):
    page.goto(
        f"{browser_server['base_url']}/reader/{browser_server['seed']['active_comic_id']}",
        wait_until="networkidle",
    )

    page.locator(".reader-container").wait_for()
    page.keyboard.press("g")

    page.get_by_role("heading", name="Go to Page").wait_for()
    page.wait_for_function(
        """
        () => {
            const modal = [...document.querySelectorAll('input[type="number"]')]
                .find((el) => el.offsetParent !== null);
            return document.activeElement === modal;
        }
        """
    )

    goto_input = page.locator('input[type="number"]').first
    goto_input.fill("2")
    page.keyboard.press("Enter")
    page.wait_for_function(
        """
        () => {
            const reader = document.querySelector('.reader-container');
            const state = reader?._x_dataStack?.[0];
            const visibleModal = [...document.querySelectorAll('input[type="number"]')]
                .find((el) => el.offsetParent !== null);
            return state?.currentPage === 1 && !visibleModal;
        }
        """
    )


@pytest.mark.browser
def test_saved_search_can_be_created_and_deleted(page, browser_server):
    page.goto(f"{browser_server['base_url']}/search", wait_until="networkidle")

    page.get_by_role("heading", name="Advanced Search").wait_for()

    first_rule = page.locator(".space-y-4 > div").first
    first_rule.locator("select").nth(0).select_option("title")
    rule_input = first_rule.locator("input[x-model='rule.value'][type='text']")
    rule_input.fill(browser_server["seed"]["active_comic_title"])

    page.get_by_role("button", name="Save").click()
    page.get_by_role("heading", name="Save Configuration").wait_for()

    modal_name_input = page.locator("input[placeholder=\"e.g. 'Alan Moore 80s'\"]")
    modal_name_input.fill("Browser Saved Search")
    page.get_by_role("button", name="Save to Search Menu").click()

    page.get_by_role("button", name="Load").click()
    saved_row = page.locator("div.cursor-pointer").filter(has_text="Browser Saved Search").first
    saved_row.wait_for()

    delete_button = saved_row.locator("button").first
    delete_button.click()

    page.get_by_role("heading", name="Delete Item?").wait_for()
    page.wait_for_function(
        """
        () => {
            const active = document.activeElement;
            return !!active && active.tagName === 'BUTTON' && active.textContent.trim() === 'Delete';
        }
        """
    )
    page.keyboard.press("Enter")

    page.wait_for_timeout(300)
    page.get_by_role("button", name="Load").click()
    page.wait_for_timeout(300)
    assert page.locator("text=Browser Saved Search").count() == 0


@pytest.mark.browser
def test_pull_list_create_modal_focuses_name_and_delete_confirms_with_keyboard(page, browser_server):
    page.goto(f"{browser_server['base_url']}/pull-lists", wait_until="networkidle")

    page.get_by_role("heading", name="My Stacks").wait_for()
    page.get_by_role("button", name="New Stack").click()

    page.get_by_role("heading", name="Create New Stack").wait_for()
    page.wait_for_function(
        """
        () => {
            const input = document.querySelector('input[placeholder="e.g. Saturday Morning Reading"]');
            return document.activeElement === input;
        }
        """
    )

    name_input = page.locator('input[placeholder="e.g. Saturday Morning Reading"]').first
    desc_input = page.locator('input[placeholder="Brief description..."]')
    name_input.fill("Browser Stack")
    desc_input.fill("Keyboard-created browser test list")
    desc_input.press("Enter")

    page.wait_for_selector("text=Browser Stack")

    list_card = page.locator("div.group").filter(has_text="Browser Stack").first
    menu_button = list_card.locator("button").first
    menu_button.click()
    page.get_by_role("button", name="Delete").click()

    page.get_by_role("heading", name="Delete Stack?").wait_for()
    page.wait_for_function(
        """
        () => {
            const active = document.activeElement;
            return !!active && active.tagName === 'BUTTON' && active.textContent.trim() === 'Delete';
        }
        """
    )
    page.keyboard.press("Enter")

    page.wait_for_timeout(300)
    page.wait_for_selector("text=You haven't created any stacks yet.")
    assert page.locator("text=Browser Stack").count() == 0
