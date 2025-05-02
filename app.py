import re
import requests
import pandas as pd
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import streamlit as st # For building the web app

# ==============================================================================
# --- Configuration Constants ---
# ==============================================================================
BASE_URL_DOMAIN = "http://books.toscrape.com/"
START_URL = "http://books.toscrape.com/catalogue/page-1.html"
REQUEST_TIMEOUT = 15
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
MAX_PAGES_TO_SCRAPE = 5

# ==============================================================================
# --- Compiled Regular Expression Patterns ---
# ==============================================================================
PRICE_REGEX = re.compile(r"£(\d+\.\d+)")
RATING_ELEMENT_REGEX = re.compile("star-rating")
RATING_WORD_REGEX = re.compile(r"star-rating\s+(One|Two|Three|Four|Five)")
STOCK_COUNT_REGEX = re.compile(r"\((\d+)\s+available\)")
TITLE_SUFFIX_REGEX = re.compile(r"\s*\([^)]+\)$")

# ==============================================================================
# --- Data Processing Functions ---
# ==============================================================================

# @st.cache_data(ttl=3600) # Cache data for 1 hour.
def extract_book_data(max_pages=MAX_PAGES_TO_SCRAPE):
    """
    Scrapes book data (title, price, rating, category, stock, url)
    from books.toscrape.com, visiting detail pages for category/stock.
    Uses Streamlit for status messages.
    """
    # Using _allow_output_mutation=True might be needed if internal objects change
    # but for returning a list, it's often fine without it. Consider if caching fails.

    st.info(f"Scraping up to {max_pages} pages ...")
    all_books = []
    session = requests.Session() # Use session for efficiency
    session.headers.update({'User-Agent': USER_AGENT})
    current_url = START_URL
    page_count = 0
    progress_bar = st.progress(0) # Add a progress bar

    while current_url and page_count < max_pages:
        progress_text = f"Scraping page {page_count + 1}/{max_pages}..."
        progress_bar.progress((page_count + 1) / max_pages, text=progress_text)
        try:
            # Fetch page content
            response = session.get(current_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status() # Check for HTTP errors
            page_count += 1
        except requests.exceptions.RequestException as e:
            st.error(f"Error fetching page {current_url}: {e}. Stopping scrape.")
            progress_bar.progress(1.0, text="Scraping stopped due to error.") # Update progress bar on error
            break # Stop on page fetch error

        soup = BeautifulSoup(response.text, "html.parser")

        # Process each book item on the page
        for article in soup.find_all("article", class_="product_pod"):
             try:
                 # --- Extract from main page ---
                 title_element = article.h3.a
                 title = title_element.get("title", "No title") if title_element else "No title"
                 price_element = article.find("p", class_="price_color")
                 price_text = price_element.text if price_element else "£0.00"
                 price_match = PRICE_REGEX.search(price_text)
                 price = float(price_match.group(1)) if price_match else 0.0
                 rating = 0 # Default
                 rating_element = article.find("p", class_=RATING_ELEMENT_REGEX)
                 if rating_element:
                     class_string = " ".join(rating_element.get("class", []))
                     rating_match = RATING_WORD_REGEX.search(class_string)
                     if rating_match:
                         rating_word = rating_match.group(1)
                         rating_map = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
                         rating = rating_map.get(rating_word, 0) # Convert word to number
                 link_element = article.h3.a
                 relative_book_url = link_element["href"] if link_element else None
                 book_url = urljoin(current_url, relative_book_url) if relative_book_url else "" # Absolute URL

                 # --- Extract from detail page ---
                 category = "Unknown"
                 stock_count = 0
                 if book_url:
                     try:
                         # Fetch book detail page
                         book_response = session.get(book_url, timeout=REQUEST_TIMEOUT)
                         book_response.raise_for_status()
                         book_soup = BeautifulSoup(book_response.text, "html.parser")
                         # Get Category
                         breadcrumb_elements = book_soup.select("ul.breadcrumb li a")
                         if len(breadcrumb_elements) >= 3 and 'category/books/' in breadcrumb_elements[2].get('href', ''):
                             category = breadcrumb_elements[2].text.strip()
                         # Get Stock count
                         stock_element = book_soup.select_one("p.instock.availability")
                         if stock_element:
                             stock_text = stock_element.get_text(strip=True)
                             stock_match = STOCK_COUNT_REGEX.search(stock_text)
                             if stock_match:
                                 stock_count = int(stock_match.group(1))
                     except requests.exceptions.RequestException: pass # Silently ignore detail page fetch errors for Streamlit
                     except Exception: pass # Silently ignore detail page parsing errors

                 # Add collected data for this book
                 all_books.append({
                     "title": title, "price": price, "rating": rating,
                     "category": category, "stock": stock_count, "url": book_url
                 })
             except Exception:
                 # Can add st.warning here if needed, but might be too verbose
                 continue # Skip book if error occurs during its processing

        # --- Find next page ---
        if page_count < max_pages:
             next_link_element = soup.select_one("li.next a")
             if next_link_element and next_link_element.get("href"):
                 current_url = urljoin(current_url, next_link_element["href"]) # Go to next page
             else:
                 current_url = None # No next link, end of site
        else:
             current_url = None # Reached max pages

    # Update progress bar to 100% when done (or if loop exited early)
    progress_bar.progress(1.0, text=f"Scraping finished. Found {len(all_books)} raw entries.")
    # Replace final print with st.success
    if len(all_books) > 0:
        st.success(f"Scraping finished. Found {len(all_books)} raw book entries.")
    else:
        st.warning("Scraping finished, but no book entries were found.")
    return all_books


@st.cache_data # Cache cleaned data
def clean_process_data(books):
    """
    Cleans the raw book data list into a Pandas DataFrame.
    Removes duplicates, handles missing values, validates URLs, standardizes fields.
    """
    if not books: return pd.DataFrame() # Return empty DataFrame if no input

    df = pd.DataFrame(books)
    initial_raw_count = len(df) # Store initial count for summary

    # Clean titles: Remove parenthetical suffixes using regex
    df['title'] = df['title'].str.replace(TITLE_SUFFIX_REGEX, "", regex=True).str.strip()

    # Remove duplicates based on the cleaned title
    df = df.drop_duplicates(subset="title", keep="first")
    duplicates_removed = initial_raw_count - len(df)

    # Handle missing/invalid required values
    initial_count = len(df)
    required_cols = ["title", "price", "rating", "url"]
    df = df.dropna(subset=required_cols)
    df = df[df['url'].str.strip() != ""]
    df = df[df['title'].str.strip().ne("") & df['title'].ne("No title available")]
    missing_removed = initial_count - len(df)

    # Validate URLs using regex
    initial_count = len(df)
    url_pattern = r"^https?://books\.toscrape\.com/catalogue/.+/index\.html$"
    df = df[df["url"].str.contains(url_pattern, regex=True, na=False)]
    invalid_url_removed = initial_count - len(df)

    # Clean category names
    if "category" in df.columns:
         df["category"] = df["category"].str.replace(r"\s+", " ", regex=True).str.strip()
         df['category'] = df['category'].replace('', 'Unknown')

    # Ensure stock is numeric integer
    if 'stock' in df.columns:
        df['stock'] = pd.to_numeric(df['stock'], errors='coerce').fillna(0).astype(int)



    return df


@st.cache_data # Cache analysis results
def analyze_data(df):
    """
    Calculates summary statistics from the cleaned DataFrame.
    """
    if df.empty:
        # Return default structure for empty data
        return {
            "total_books": 0, "average_price": 0, "max_price": 0, "min_price": 0,
            "price_distribution": {}, "category_distribution": {}, "rating_distribution": {},
            "average_stock": 0, "total_stock": 0,
            "price_rating_corr": 0.0 # Add correlation default
        }

    # Calculate metrics
    analysis = {
        "total_books": len(df),
        "average_price": df["price"].mean(),
        "max_price": df["price"].max(),
        "min_price": df["price"].min(),
        "price_distribution": df["price"].describe().to_dict(), # Price summary
        "category_distribution": df["category"].value_counts().to_dict(), # Category counts
        "rating_distribution": df["rating"].value_counts().sort_index().to_dict(), # Rating counts
        "average_stock": df["stock"].mean() if "stock" in df.columns else 0, # Avg stock (if available)
        "total_stock": df["stock"].sum() if "stock" in df.columns else 0,    # Total stock (if available)
        # Calculate correlation here
        "price_rating_corr": df['price'].corr(df['rating']) if not df['price'].isnull().all() and not df['rating'].isnull().all() else 0.0
    }
    return analysis

# ==============================================================================
# --- Visualization Function  ---
# ==============================================================================
# Not cached - generates plot based on current (cached) analysis data
def create_visualizations(df, analysis):
    """
    Generates Matplotlib plots (price hist, category bar, rating pie, price vs rating scatter).
    Returns the figure object.
    """
    if df.empty or not analysis or analysis.get("total_books", 0) == 0:
        st.warning("No data available to generate visualizations.")
        return None # No data, no figure

    # Adjusted figsize for a better 2x2 layout
    fig = plt.figure(figsize=(12, 10)) # Make height similar to width/2

    # --- Subplot 1: Price distribution histogram ---
    ax1 = fig.add_subplot(2, 2, 1) # Top-left
    if not df["price"].empty:
        df["price"].plot.hist(bins=15, edgecolor="black", ax=ax1) # Plot on axis
        ax1.set_title("Price Distribution")
        ax1.set_xlabel("Price (£)")
        ax1.set_ylabel("Frequency")
    else:
        ax1.set_title("Price Distribution (No Data)")

    # --- Subplot 2: Category distribution bar chart (Top 15) ---
    ax2 = fig.add_subplot(2, 2, 2) # Top-right
    category_data = analysis.get("category_distribution", {})
    if category_data:
        top_categories = pd.Series(category_data).sort_values(ascending=False).head(15)
        top_categories.plot.bar(ax=ax2) # Plot on axis
        # Use clearer title
        ax2.set_title("Top 15 Categories by Book Count")
        ax2.set_ylabel("Number of Books")
        plt.setp(ax2.get_xticklabels(), rotation=45, ha="right") # Rotate labels
    else:
        ax2.set_title("Books per Category (No Data)")

    # --- Subplot 3: Rating distribution pie chart ---
    ax3 = fig.add_subplot(2, 2, 3) # Bottom-left
    rating_data = analysis.get("rating_distribution", {})
    if rating_data:
        rating_series = pd.Series(rating_data).sort_index() # Sort ratings 1-5
        ax3.pie(rating_series, labels=rating_series.index, autopct="%1.1f%%", startangle=90, counterclock=False)
        ax3.set_title("Rating Distribution")
        ax3.set_ylabel("") # Hide default pie label
    else:
        ax3.set_title("Rating Distribution (No Data)")

    # --- Subplot 4: Price vs. Rating scatter plot ---
    ax4 = fig.add_subplot(2, 2, 4) # Bottom-right
    # Use the 'df' passed into the function
    if not df["price"].empty and not df["rating"].empty:
        ax4.scatter(df['price'], df['rating'], alpha=0.5) # alpha for transparency
        ax4.set_title('Book Price vs. Rating')
        ax4.set_xlabel('Price (£)')
        ax4.set_ylabel('Rating (1-5)')
        ax4.grid(True) # Add grid for easier reading
    else:
        ax4.set_title('Book Price vs. Rating (No Data)')

    # --- Final adjustments ---
    fig.tight_layout() # Adjust layout AFTER all subplots are defined
    return fig # Return figure for st.pyplot()

# ==============================================================================
# --- Streamlit Application UI ---
# ==============================================================================

st.set_page_config(layout="wide") # Use wide page format
st.title("📚 Books.toscrape Analysis Dashboard")
st.markdown(f"Analysis based on the first **{MAX_PAGES_TO_SCRAPE}** pages scraped live.")

# --- Load/Process Data ---
# Show spinner during potentially long operations
# Use columns to potentially place spinner next to a message if desired
col_load1, col_load2 = st.columns([1, 5])
with col_load1:
    st.markdown(" **Status:** ") # Placeholder or status icon area

with col_load2:
    with st.spinner(f"Loading data (up to {MAX_PAGES_TO_SCRAPE} pages)..."):
        # Use cached functions for speed
        raw_books_data = extract_book_data(max_pages=MAX_PAGES_TO_SCRAPE)
        data_loaded = False # Track success

        if raw_books_data:
            cleaned_df = clean_process_data(raw_books_data)
            if not cleaned_df.empty:
                analysis_results = analyze_data(cleaned_df)
                data_loaded = True # Success!
            else:
                st.error("Data cleaning resulted in an empty dataset.")
                analysis_results = analyze_data(cleaned_df) # Get default structure
                cleaned_df = pd.DataFrame() # Ensure df is empty DataFrame
        else:
            st.error("Failed to scrape book data.")
            analysis_results = analyze_data(pd.DataFrame()) # Get default structure
            cleaned_df = pd.DataFrame() # Ensure df is empty DataFrame


# --- Display Results ---
if data_loaded: # Only show if data is ready
    st.header("📊 Key Metrics & Analysis")

    # --- Row 1: Basic Counts & Averages ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Books Analyzed", analysis_results['total_books'])
    col2.metric("Average Price", f"£{analysis_results['average_price']:.2f}")
    col3.metric("Average Stock", f"{analysis_results.get('average_stock', 0):.1f}")
    col4.metric("Total Stock", f"{analysis_results.get('total_stock', 0)}")

    # --- Row 2: Price Details & Correlation ---
    col5, col6, col7 = st.columns(3)
    col5.metric("Minimum Price", f"£{analysis_results['min_price']:.2f}")
    col6.metric("Maximum Price", f"£{analysis_results['max_price']:.2f}")
    col7.metric("Price/Rating Correlation", f"{analysis_results.get('price_rating_corr', 0.0):.3f}")


    # --- Detailed Distributions (Collapsible) ---
    with st.expander("Detailed Distributions"):
        dist_col1, dist_col2 = st.columns(2)
        with dist_col1:
            st.subheader("Price Distribution Statistics")
            # Use st.json for nicely formatted dictionary/JSON display
            st.json(analysis_results.get('price_distribution', {}))
        with dist_col2:
            st.subheader("Rating Distribution (Rating: Count)")
            # Sort ratings before displaying
            sorted_ratings = dict(sorted(analysis_results.get('rating_distribution', {}).items()))
            st.json(sorted_ratings)


    # --- Visualizations ---
    st.header("📈 Visualizations")
    # Generate the updated 2x2 Matplotlib figure
    fig = create_visualizations(cleaned_df, analysis_results)
    if fig:
        st.pyplot(fig) # Display Matplotlib figure in Streamlit
    else:
        st.warning("Could not generate visualizations (likely no data).")


    # --- Optional Data Table ---
    st.header(" Glimpse at Cleaned Data")
    # Display the first few rows of the cleaned data
    st.dataframe(cleaned_df.head(10))

    # Add a checkbox to allow users to view the full dataset.
    if st.checkbox("Show Full Cleaned Data Table"):
        st.dataframe(cleaned_df)
else:
    # Fallback message if data loading failed
    st.error("Application could not load or process data to display.")

# --- Footer ---
st.markdown("---")
st.markdown("Streamlit App based on Web Scraping Project")